# Mewgenics Breeding Manager

A high-performance, Python-based tool for optimizing breeding operations in Mewgenics. It extracts data directly from your save files and helps you compare pairings, optimize room layouts, and plan long-term lines to maximize strong offspring while minimizing inbreeding risk.

Current release: `v5.4.0`

If you'd like to support the project, you can [here](https://ko-fi.com/frankieg33).

## Screenshots

### Main Page

![Main Page](Sceenshots/Home%20Screen.png)

### Breeding Optimizer

![Breeding Optimizer](Sceenshots/Room%20Optimizer.png)

### Perfect 7 Planner

![Perfect 7 Planner](Sceenshots/Perfect%207%20Planner.png)

## Core Features

- Load your save file and keep your full roster, lineage, and relationships in one place
- Compare pairings with inheritance odds, expected offspring stats, and risk
- Optimize room layouts with movement-aware scoring
- Plan long-term perfect-stat lines with the Perfect 7 planner
- Read ability and mutation text from `resources.gpak` when available
- Cat sprite rendering with DefinedShape extraction from GPAK

## Install

This project uses `pip` and `requirements.txt`.

```bash
git clone https://github.com/frankieg33/MewgenicsBreedingManager
cd MewgenicsBreedingManager
pip install -r requirements.txt
python src/mewgenics_manager.py
```

The app will automatically look for `resources.gpak` in common Steam install paths, the app's configured save root, or in the current working directory.

## Build

```bash
build.bat
```

On Linux, use `build.sh`.

## Requirements

- Python 3.14
- PySide6
- lz4
- openpyxl

## Attribution & Licenses

This project uses third-party libraries with their respective licenses:

- **ffdec_lib** ([JPEXS Free Flash Decompiler](https://github.com/jindrapetrik/jpexs-decompiler))
  - Used under LGPL license to parse image data from game GPAK archives
  - Source code available at: https://github.com/jindrapetrik/jpexs-decompiler/releases
  - A copy of the LGPL license is included in [LICENSE-LGPL.txt](./src/CatAssets/ffdec_lib_26.0.0/license.txt)

## Credits

- Save parsing research based on [pzx521521/mewgenics-save-editor](https://github.com/pzx521521/mewgenics-save-editor)
- Community reverse-engineering help from players and mod users
- PR contributors: [0demongamer0](https://github.com/0demongamer0), [An-on-im](https://github.com/An-on-im), [byronaltice](https://github.com/byronaltice), [heartskingu](https://github.com/heartskingu), [ICaxapl](https://github.com/ICaxapl), [luisMolina95](https://github.com/luisMolina95), [TheMegax](https://github.com/TheMegax), [dasfoxx](https://github.com/dasfoxx)
- Simulated annealing (SA) idea from [PurpleMyst](https://github.com/PurpleMyst/mewgenics_breeding_helper)
- Original idea and reference from frankieg33

## Release Notes

### v5.4.0

- Improved tooltip coverage across all views with full localization support
- Updated onboarding tutorial with cat sprite rendering and shape extraction info
- Updated What's New dialog for v5.4.0 release content
- Added new locale keys for tooltips and dialogs across all 4 languages (en, zh_CN, ru, pl)
- Added tests for shape extraction, localization, configuration, and dialog modules
- Wrapped hardcoded tooltips in `_tr()` across breeding partners, calibration, family tree, room priority, furniture, safe breeding, and cat detail views

### v5.3.1

- Added automatic DefinedShape extraction from `resources.gpak` for cat sprite rendering
- Bundled `DefinedShapes.zip` (16.5 MB) so cloners can render cat sprites without the game installed
- Startup extraction chain: cached PNGs (instant) -> ZIP (~3 s) -> GPAK (~25 s)
- Replaced `.rar` distribution with `.zip` for cross-platform compatibility
- New module: `utils/shape_extractor.py` — SWF shape parsing and Qt-based rendering

### v5.2.0

- Added class-specific mutation trees for `Best Pairs`, `Melee`, `Ranged`, and `Magic`, with per-room mode scoring in the room optimizer
- Moved class stat-priority editing into the room distributor with dedicated class stats controls and recommended reset actions
- Updated mutation-planner trait visibility with clearer effects, softer wanted/avoid coloring, and cleaner mutation descriptions
- Kept Perfect 7 Planner tied to `Best Pairs` mutations only so its imports stay predictable
- Refined persistence, migration, and UI coverage for the new mutation-class workflow

### v5.0.0

- Full codebase refactoring: split monolithic `mewgenics_manager.py` (~19k lines) into a structured `mewgenics/` package
- New package layout: `utils/`, `models/`, `workers/`, `views/`, `panels/`, `dialogs` — 30+ focused modules
- Entry point (`mewgenics_manager.py`) is now a thin wrapper for backwards compatibility
- No feature changes or behavior differences — pure structural refactor
- Updated PyInstaller spec with all new submodule imports

### v4.4.1

- Follow-up release for the same planner, optimizer, localization, and test updates shipped in `v4.4.0`
- Keeps the shared optimizer search settings, deeper room optimizer controls, breeding partner improvements, and planner persistence updates in sync with the latest release line

### v4.4.0

- Added shared optimizer search settings so the room optimizer and Perfect 7 planner use the same simulated annealing controls
- Expanded the room optimizer with deeper search options, clearer setup/configuration tabs, and improved room-related tooltips
- Improved the breeding partners view to distinguish mutual and one-way love links
- Refined the mutation planner so cats are shown alongside selected traits instead of being buried behind room filters
- Updated the saved UI defaults and persistence behavior for the new planner and optimizer settings
- Expanded localization coverage for the new settings, labels, and status messages
- Added and updated tests around planner persistence, optimizer behavior, trait labels, and UI interactions
