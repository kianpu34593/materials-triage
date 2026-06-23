"""Behavioral tests for the ``doctor`` environment self-check."""

import io

from materials_triage.doctor import Check, check_environment, format_report, run_doctor


def _aws(check):
    return next(c for c in check if "AWS" in c.name or "Bedrock" in c.name)


def test_missing_materials_project_key_is_a_failing_required_check():
    checks = check_environment({}, aws_creds_present=lambda: False)
    mp = next(c for c in checks if "X_API_KEY" in c.detail or "Materials Project" in c.name)
    assert mp.required is True
    assert mp.ok is False


def test_openalex_mailto_is_optional_not_required():
    missing = check_environment({}, aws_creds_present=lambda: True)
    mailto = next(c for c in missing if "OpenAlex" in c.name)
    assert mailto.required is False
    assert mailto.ok is False

    present = check_environment({"OPENALEX_MAILTO": "me@lab.org"}, aws_creds_present=lambda: True)
    assert next(c for c in present if "OpenAlex" in c.name).ok is True


def test_doctor_subcommand_runs_the_checklist(capsys):
    from materials_triage.cli import main

    code = main(["doctor"])
    captured = capsys.readouterr().out
    assert code in (0, 1)
    assert "Materials Project" in captured
    assert "AWS" in captured


def test_run_doctor_exit_code_reflects_required_checks():
    out = io.StringIO()
    ok = run_doctor({"X_API_KEY": "k"}, aws_creds_present=lambda: True, out=out)
    assert ok == 0
    assert "Materials Project" in out.getvalue()

    bad = run_doctor({}, aws_creds_present=lambda: False, out=io.StringIO())
    assert bad == 1


def test_format_report_marks_pass_fail_and_shows_detail():
    report = format_report(
        (
            Check(name="Good", ok=True, detail="all set", required=True),
            Check(name="Bad", ok=False, detail="missing thing", required=True),
        )
    )
    lines = report.splitlines()
    good = next(line for line in lines if "Good" in line)
    bad = next(line for line in lines if "Bad" in line)
    assert "✓" in good
    assert "✗" in bad
    assert "missing thing" in bad


def test_aws_credentials_use_the_injected_probe():
    unresolved = check_environment({}, aws_creds_present=lambda: False)
    assert _aws(unresolved).ok is False
    assert _aws(unresolved).required is True

    resolved = check_environment({}, aws_creds_present=lambda: True)
    assert _aws(resolved).ok is True
