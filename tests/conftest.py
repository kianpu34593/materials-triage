"""Pytest bootstrap shared by the whole suite.

Loads a local ``.env`` (if present) at collection time so the live tests can read
their credentials from it — X_API_KEY (Materials Project), OPENALEX_MAILTO, and
the AWS_* vars (Bedrock). This must run before the test modules are imported,
because each live test's ``@pytest.mark.skipif`` reads ``os.environ`` at
collection time; pytest imports conftest.py ahead of the test modules, so the
vars are present by the time those guards are evaluated.

``load_dotenv`` does not override variables already exported in the shell
(real exports win), strips surrounding quotes, and is a no-op when there is no
``.env``. The import is optional so offline runs and CI — which never have a
``.env`` and need no credentials — work without python-dotenv installed.
"""

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()
