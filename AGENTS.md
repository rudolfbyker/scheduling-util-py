## Environment

- Always activate the `venv`.
- Always set `PYTHONPATH` to the `src` folder.

## Code formatting

I use `black` but you should not run it.

## Type checking

- Use `PYTHONPATH=src venv/bin/mypy`.
- Never run `mypy` on ignored files.
- Install requirements using `venv/bin/pip install -r mypy-requirements.txt` if necessary.
- To check the entire repo: `venv/bin/mypy .`.

## Unit testing

- To run all tests: `PYTHONPATH=src venv/bin/python -m unittest`.
- Install requirements using `venv/bin/pip install -r test-requirements.txt` if necessary.

## Ignored files

- Always respect `.gitignore`.
- Never run linters or tests on ignored files or folders.
