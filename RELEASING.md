# Releasing `arangodb-schema-analyzer`

This document describes how to cut a release and publish it to PyPI.

The project uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC), so no long-lived API tokens are stored in the repo. See
`.github/workflows/publish.yml`.

## One-time setup (PyPI side)

1. Sign in to <https://pypi.org> (and/or <https://test.pypi.org> for dry runs).
2. Navigate to the project, or create a **pending publisher** before the first
   upload:
   - Account → *Your projects* → *Publishing* → *Add a new pending publisher*
   - PyPI project name: `arangodb-schema-analyzer`
   - Owner: `ArthurKeen`
   - Repository name: `arango-schema-mapper`
   - Workflow name: `publish.yml`
   - Environment name: `pypi` (use `testpypi` for TestPyPI)
3. Repeat on TestPyPI if you want dry-run uploads.

## One-time setup (GitHub side)

1. Repo → *Settings* → *Environments* → create an environment named `pypi`
   (and optionally `testpypi`). No secrets are required.
2. Recommended: add required reviewers on the `pypi` environment so every
   release requires an approval click.

## Cutting a release

1. Make sure `main` is green (CI + integration where applicable) and contains
   everything you want to ship.
2. Update the version in `pyproject.toml` (single source of truth).
3. Update `CHANGELOG.md` with a new section for the version.
4. Commit + open a PR, merge to `main`.
5. Tag the release commit on `main`:

   ```bash
   git checkout main
   git pull
   git tag -a v0.1.0 -m "Release 0.1.0"
   git push origin v0.1.0
   ```

6. The tag push triggers `.github/workflows/publish.yml`, which builds the
   sdist + wheel, runs `twine check --strict`, and uploads to PyPI via OIDC.
7. Create a GitHub Release from the tag (optional but recommended) and paste
   the changelog section into the release notes.

## TestPyPI dry run

Before an important release, push a dry run to TestPyPI:

1. GitHub → *Actions* → *Publish to PyPI* → *Run workflow*.
2. Select branch `main`, set `target` to `testpypi`.
3. Verify the upload at <https://test.pypi.org/project/arangodb-schema-analyzer/>.
4. Install and smoke-test:

   ```bash
   python -m pip install --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     arangodb-schema-analyzer
   arangodb-schema-analyzer --help
   ```

## Building locally

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check --strict dist/*
```

This produces `dist/arangodb_schema_analyzer-<version>-py3-none-any.whl` and
`dist/arangodb_schema_analyzer-<version>.tar.gz`.

## Manual upload (emergency / fallback)

Only if Trusted Publishing is unavailable:

```bash
python -m pip install --upgrade twine
python -m twine upload dist/*            # PyPI (prompts for API token)
python -m twine upload -r testpypi dist/*  # TestPyPI
```

Store the token in `~/.pypirc` **or** via the `TWINE_PASSWORD` env var — never
in the repo.

## Versioning

- Follow SemVer: `MAJOR.MINOR.PATCH`.
- Pre-`1.0.0`, bump `MINOR` for any user-visible change, `PATCH` for fixes.
- Version lives in `pyproject.toml`; update in one place only.

## Post-release checklist

- [ ] `pip install arangodb-schema-analyzer` works on a clean machine
- [ ] `arangodb-schema-analyzer --help` prints
- [ ] `python -c "import schema_analyzer; print(schema_analyzer.__all__)"` works
- [ ] GitHub Release created with notes
- [ ] `CHANGELOG.md` has an `Unreleased` section ready for the next round
