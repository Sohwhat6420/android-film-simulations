# Contributing

Thanks for looking. Issues and pull requests are welcome, especially results from phones other than the POCO X6 Pro.

## Before you start

- **Never commit Fujifilm data.** Fujifilm's LUTs are free to download, but their terms forbid redistribution. That
  covers the LUTs, anything built from them (`.cube` files, targets, `.npy` arrays) and photos rendered through them.
  `.gitignore` already keeps `sources/`, `targets/`, `*.cube` and `*.npy` out; please don't work around it.
- **No phone captures either.** `captures/` stays local. Results go in as numbers only, like
  `pilot/results/poco-x6-pro.json`.
- **Every published number comes from real shots.** The fitted model only ranks candidates; please keep it that way.

## Running the checks

```bash
pip install -r pilot/requirements.txt
python -X utf8 pilot/tests/smoke_test.py    # offline, about 4 s; CI runs it on Ubuntu and Windows
```

## Adding a phone

1. Follow [pilot/METHOD.md](pilot/METHOD.md) with your phone (Wireless debugging on, `ADB_SERIAL` set).
2. Add `pilot/results/<phone>.json` in the same shape as the POCO file, with the dates, the shot count and the rig's
   repeat noise.
3. Open a pull request with the phone, its camera app version, and anything the method had to change for it.

## Style

- Python 3.11+, standard scientific stack only (numpy, scipy, colour-science, OpenCV, Pillow).
- British English in docs and comments.
- Small, focused pull requests with a one-line reason in the title.

## Good first issues

Look for the [`good first issue`](https://github.com/Sohwhat6420/android-film-simulations/labels/good%20first%20issue)
label, and ask in the issue if anything is unclear.
