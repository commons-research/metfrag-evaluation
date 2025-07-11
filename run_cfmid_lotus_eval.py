import os
import typing as T

import numpy as np
import pandas as pd
from cache_decorator import Cache
from downloaders import BaseDownloader
from matchms import calculate_scores
from matchms.filtering import default_filters
from matchms.importing import load_from_mgf
from matchms.similarity import CosineGreedy, PrecursorMzMatch
from tqdm import tqdm, trange

from metfrag_evaluation.massspecgym import load_massspecgym, to_spectra
from metfrag_evaluation.spectrum import Spectrum


def download_isdb() -> None:
    downloader = BaseDownloader(auto_extract=False)
    _ = downloader.download(
        "https://zenodo.org/records/14887271/files/isdb_lotus_pos_energySum.mgf",
        "data/isdb/isdb_lotus_pos_energySum.mgf",
    )


@Cache()
def load_isdb() -> T.List[Spectrum]:
    """Load ISDB spectra from MGF file."""
    spectra = []
    for spectrum in tqdm(
        load_from_mgf("data/isdb/isdb_lotus_pos_energySum.mgf"),
        desc="Loading ISDB spectra",
        leave=False,
    ):
        spectrum = default_filters(spectrum)
        spectrum = Spectrum(
            mz=spectrum.mz,
            intensities=spectrum.intensities,
            metadata=spectrum.metadata,
        )
        spectra.append(spectrum)

    return spectra


def filter_massspecgym_spectra(
    massspecgym_spectra: T.List[Spectrum], isdb_spectra: T.List[Spectrum]
) -> T.List[Spectrum]:
    """Filter MassSpecGym spectra to only include those present in ISDB."""
    isdb_inchikeys = set(s.get("compound_name") for s in isdb_spectra)
    filtered_spectra = [
        s
        for s in tqdm(
            massspecgym_spectra, leave=False, desc="Filtering MassSpecGym spectra"
        )
        if s.get("inchikey") in isdb_inchikeys
    ]
    filtered_spectra = [s for s in filtered_spectra if s.get("adduct") == "[M+H]+"]
    return filtered_spectra


def main():
    download_isdb()
    massspecgym = load_massspecgym()
    spectra: T.List[Spectrum] = to_spectra(massspecgym)
    isdb: T.List[Spectrum] = load_isdb()

    # we filter the MassSpecGym spectra to only include those present in ISDB
    spectra = filter_massspecgym_spectra(spectra, isdb)

    similarity_score = PrecursorMzMatch(tolerance=10.0, tolerance_type="ppm")
    chunks_query = [spectra[x : x + 1000] for x in range(0, len(spectra), 1000)]

    cosinegreedy = CosineGreedy(tolerance=0.01)

    scans_id_map = {}
    i = 0
    for chunk_number, chunk in enumerate(tqdm(chunks_query)):
        scores = calculate_scores(chunk, isdb, similarity_score)
        idx_row = scores.scores[:, :][0]
        idx_col = scores.scores[:, :][1]

        for _ in chunk:
            scans_id_map[i] = i
            i += 1

        data = []
        for x, y in zip(idx_row, idx_col):
            if x >= y:
                continue
            msms_score, n_matches = cosinegreedy.pair(chunk[x], isdb[y])[()]
            # if (msms_score > 0.2) and (n_matches > 6):

            feature_id = scans_id_map[int(x) + int(1000 * chunk_number)]
            data.append(
                {
                    "msms_score": msms_score,
                    "matched_peaks": n_matches,
                    "feature_id": feature_id,
                    "reference_id": y
                    + 1,  # code copied from https://github.com/mandelbrot-project/met_annot_enhancer/blob/f8346fd3f7a9775d1d6638cf091d019167ba7ce1/src/dev/spectral_lib_matcher.py#L175
                    "inchikey_isdb": isdb[y].get("compound_name"),
                    "inchikey_msg": chunk[x].get("inchikey"),
                    "adduct": chunk[x].get("adduct"),
                    "instrument": chunk[x].get("instrument_type"),
                }
            )
        df = pd.DataFrame(data)
        df.to_csv(
            "lotus_cfmid_scores.csv",
            mode="a",
            header=not os.path.exists("lotus_cfmid_scores.csv"),
            sep=",",
            index=False,
        )


if __name__ == "__main__":
    main()
