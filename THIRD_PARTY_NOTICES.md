# Third-party notices

Sonar Studio runs two outside programs. It does not include, copy or import their code. It writes input files for them, starts them in their own conda envs with the `.cmd` launchers, and reads the files they write.

| Program | License | Source of the license statement | Code |
|---|---|---|---|
| GhostVision | GPL-3.0-or-later | LICENSE file in the repository. Its package metadata says MIT; the LICENSE file governs. | https://github.com/PINGEcosystem/GhostVision |
| RockMapper | MIT | Classifier in the installed package metadata. The repository root had no LICENSE file on 23 October 2026. | https://github.com/PINGEcosystem/RockMapper |
| PINGInstaller | See its repository | Installs and upgrades both programs; the `.cmd` launchers call it. | https://github.com/CameronBodine/PINGInstaller |

`testdata/` holds small samples of output that these programs wrote on synthetic Sonar Studio images: 20 rows of a GhostVision `detections.csv` with their tile index, and a 720 x 370 px slice of a RockMapper class mosaic with the 20 x 10 px GeoTIFF it classified.

Python dependencies (numpy, scipy, Pillow) install separately from `requirements.txt` under their own licenses.
