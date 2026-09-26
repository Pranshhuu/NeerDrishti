"""
Environment verification tests for FlowSight Phase 1

Exercises app.core.environment: whether the checker correctly detects the
runtime dependencies FlowSight needs, reports their versions, and aggregates
that into a usable report.

Testing strategy:
    The checker takes no injectable dependencies: every check either imports
    inside a try block or shells out through subprocess.run. Those two
    boundaries are therefore where the tests control behaviour — import is
    intercepted via builtins.__import__, and command execution via
    subprocess.run in the environment module's namespace.

    This matters because the tests must verify the checker, not the machine
    running pytest. An assertion that GDAL is available merely because it
    happens to be installed on a developer's Mac would pass in CI for the wrong
    reason and fail on a fresh checkout for the right one.

Versions:
    No test asserts a specific version number. Versions are supplied by stubs,
    so the assertions verify that the checker extracts and reports what a tool
    told it, not what any particular machine has installed.

No external calls:
    No GDAL command runs, no WhiteboxTools instance is created, nothing is
    downloaded, and no network access occurs.
"""

import builtins
import subprocess
import sys

import pytest

from app.core import environment as environment_module
from app.core.environment import (
    EnvironmentChecker,
    EnvironmentStatus,
    format_environment_report,
    get_environment_report,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _block_imports(monkeypatch, *blocked: str) -> None:
    """
    Make the named modules raise ImportError when imported.

    The checker imports inside try blocks, so intercepting builtins.__import__
    is the boundary at which a missing dependency can be simulated on a machine
    where it is installed.

    Args:
        monkeypatch: pytest's patching fixture, which restores the original.
        *blocked: Module names to make unavailable.
    """
    blocked_names = set(blocked)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        root = name.split(".")[0]
        if name in blocked_names or root in blocked_names:
            raise ImportError(f"simulated missing module: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    # A module already in sys.modules is returned without calling __import__,
    # so cached entries must be removed for the interception to take effect.
    for name in list(sys.modules):
        if name.split(".")[0] in blocked_names:
            monkeypatch.delitem(sys.modules, name, raising=False)


def _stub_module(monkeypatch, name: str, module) -> None:
    """
    Install a stand-in module so an import inside the checker returns it.

    Args:
        monkeypatch: pytest's patching fixture.
        name: Module name to replace.
        module: Object returned in its place.
    """
    monkeypatch.setitem(sys.modules, name, module)


def _stub_gdal_cli(monkeypatch, stdout: str, returncode: int = 0) -> None:
    """
    Replace command execution with a stub returning fixed output.

    Patches subprocess.run inside the environment module's namespace, which is
    where the checker looks it up.

    Args:
        monkeypatch: pytest's patching fixture.
        stdout: Text the stubbed command writes.
        returncode: Exit code the stubbed command returns.
    """

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(environment_module.subprocess, "run", fake_run)


def _raise_from_gdal_cli(monkeypatch, error: Exception) -> None:
    """
    Make command execution raise, to exercise the checker's error handling.

    Args:
        monkeypatch: pytest's patching fixture.
        error: Exception the stubbed command raises.
    """

    def failing_run(args, **kwargs):
        raise error

    monkeypatch.setattr(environment_module.subprocess, "run", failing_run)


class _FakeWhitebox:
    """Stand-in for the whitebox module, returning a fixed version banner."""

    def __init__(self, banner: str = "WhiteboxTools v2.0.0 by Dr. John B. Lindsay"):
        self._banner = banner

    def WhiteboxTools(self):
        """Return a stand-in tools object without starting anything."""
        banner = self._banner

        class _Tools:
            def version(self_inner):
                return banner

        return _Tools()


# ----------------------------------------------------------------------
# Python version
# ----------------------------------------------------------------------


def test_reports_running_python_version():
    """
    The reported Python version matches the interpreter running the tests.

    This is the one value the checker can read directly rather than probe for,
    so it is asserted against sys.version_info rather than a literal.
    """
    version = EnvironmentChecker.get_python_version()

    assert version == (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )


def test_python_version_has_three_components():
    """The version string is major.minor.micro."""
    parts = EnvironmentChecker.get_python_version().split(".")

    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


# ----------------------------------------------------------------------
# GDAL CLI detection
# ----------------------------------------------------------------------


def test_gdal_cli_detected_from_version_banner(monkeypatch):
    """
    A successful version banner yields availability and the parsed version.

    The version is taken from the stub, so the assertion confirms extraction
    rather than what any machine has installed.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1, released 2024/02/01")

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is True
    assert version == "3.8.1"


def test_gdal_cli_version_parsed_from_arbitrary_banner(monkeypatch):
    """A different version number is extracted just as accurately."""
    _stub_gdal_cli(monkeypatch, "GDAL 3.6.4, released 2023/04/17")

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is True
    assert version == "3.6.4"


def test_gdal_cli_reports_raw_output_when_unparseable(monkeypatch):
    """
    An unrecognised banner is reported verbatim rather than discarded.

    A tool that responds but in an unexpected format is still present; keeping
    its output gives an operator something to diagnose.
    """
    _stub_gdal_cli(monkeypatch, "some unexpected output")

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is True
    assert version == "some unexpected output"


def test_gdal_cli_unavailable_on_nonzero_exit(monkeypatch):
    """A non-zero exit code means the CLI is not usable."""
    _stub_gdal_cli(monkeypatch, "", returncode=1)

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is False
    assert version is None


def test_gdal_cli_unavailable_when_executable_missing(monkeypatch):
    """
    A missing executable is reported as unavailable, not raised.

    FileNotFoundError is the normal signal that GDAL is not installed, so it
    must produce a report rather than an exception.
    """
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is False
    assert version is None


def test_gdal_cli_unavailable_on_timeout(monkeypatch):
    """A command that hangs is reported as unavailable rather than blocking."""
    _raise_from_gdal_cli(
        monkeypatch, subprocess.TimeoutExpired(cmd=["gdalinfo"], timeout=5)
    )

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is False
    assert version is None


def test_gdal_cli_unavailable_on_os_error(monkeypatch):
    """An OS-level execution failure is reported, not raised."""
    _raise_from_gdal_cli(monkeypatch, OSError("permission denied"))

    available, version = EnvironmentChecker.check_gdal_cli()

    assert available is False
    assert version is None


def test_gdal_cli_probes_a_single_executable(monkeypatch):
    """
    Detection runs one command rather than probing each tool separately.

    gdalwarp and gdaldem ship with the same GDAL installation, so one probe
    establishes availability. Asserting this pins the behaviour: a test that
    expected three calls would fail, and one that expected any number would
    verify nothing.
    """
    invocations = []

    def recording_run(args, **kwargs):
        invocations.append(list(args))
        return subprocess.CompletedProcess(args, 0, "GDAL 3.8.1", "")

    monkeypatch.setattr(environment_module.subprocess, "run", recording_run)

    EnvironmentChecker.check_gdal_cli()

    assert len(invocations) == 1
    assert invocations[0][0] == "gdalinfo"


def test_gdal_cli_probe_is_bounded_by_a_timeout(monkeypatch):
    """
    The probe carries a timeout so detection cannot hang.

    Environment checking runs on API status requests, where an unbounded wait
    on a wedged binary would stall the response.
    """
    captured = {}

    def recording_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "GDAL 3.8.1", "")

    monkeypatch.setattr(environment_module.subprocess, "run", recording_run)

    EnvironmentChecker.check_gdal_cli()

    assert "timeout" in captured
    assert captured["timeout"] > 0


# ----------------------------------------------------------------------
# Python package detection
# ----------------------------------------------------------------------


def test_rasterio_reported_with_stubbed_version(monkeypatch):
    """
    rasterio availability and version come from the imported module.

    The version is supplied by a stub so the assertion holds on any machine.
    """
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )

    available, version = EnvironmentChecker.check_rasterio()

    assert available is True
    assert version == "1.3.8"


def test_rasterio_unavailable_when_import_fails(monkeypatch):
    """A missing rasterio is reported as unavailable, not raised."""
    _block_imports(monkeypatch, "rasterio")

    available, version = EnvironmentChecker.check_rasterio()

    assert available is False
    assert version is None


def test_pyproj_reported_with_stubbed_version(monkeypatch):
    """pyproj availability and version come from the imported module."""
    _stub_module(monkeypatch, "pyproj", type("pyproj", (), {"__version__": "3.6.1"}))

    available, version = EnvironmentChecker.check_pyproj()

    assert available is True
    assert version == "3.6.1"


def test_pyproj_unavailable_when_import_fails(monkeypatch):
    """A missing pyproj is reported as unavailable."""
    _block_imports(monkeypatch, "pyproj")

    available, version = EnvironmentChecker.check_pyproj()

    assert available is False
    assert version is None


def test_numpy_reported_with_stubbed_version(monkeypatch):
    """numpy availability and version come from the imported module."""
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    available, version = EnvironmentChecker.check_numpy_available()

    assert available is True
    assert version == "1.26.2"


def test_numpy_unavailable_when_import_fails(monkeypatch):
    """A missing numpy is reported as unavailable."""
    _block_imports(monkeypatch, "numpy")

    available, version = EnvironmentChecker.check_numpy_available()

    assert available is False
    assert version is None


def test_gdal_python_reported_with_stubbed_version(monkeypatch):
    """The GDAL Python binding's version is read from the module."""
    fake_osgeo = type("osgeo", (), {})
    fake_osgeo.gdal = type("gdal", (), {"__version__": "3.8.1"})

    _stub_module(monkeypatch, "osgeo", fake_osgeo)
    _stub_module(monkeypatch, "osgeo.gdal", fake_osgeo.gdal)

    available, version = EnvironmentChecker.check_gdal_python()

    assert available is True
    assert version == "3.8.1"


def test_gdal_python_unavailable_when_import_fails(monkeypatch):
    """
    Absent GDAL bindings are reported as unavailable.

    This is the common case: the CLI is frequently installed without the Python
    bindings, and the checker reports the two separately for that reason.
    """
    _block_imports(monkeypatch, "osgeo")

    available, version = EnvironmentChecker.check_gdal_python()

    assert available is False
    assert version is None


# ----------------------------------------------------------------------
# WhiteboxTools detection
# ----------------------------------------------------------------------


def test_whitebox_version_parsed_from_banner(monkeypatch):
    """
    The WhiteboxTools version is extracted from its version banner.

    The banner comes from a stub, so no real tool is instantiated and no binary
    is downloaded.
    """
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())

    available, version = EnvironmentChecker.check_whitebox()

    assert available is True
    assert version == "2.0.0"


def test_whitebox_version_parsed_from_alternative_banner(monkeypatch):
    """A different version in the banner is extracted just as accurately."""
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox("WhiteboxTools v2.3.0"))

    available, version = EnvironmentChecker.check_whitebox()

    assert available is True
    assert version == "2.3.0"


def test_whitebox_version_unknown_when_banner_unparseable(monkeypatch):
    """
    An unrecognised banner yields 'unknown' while remaining available.

    The package imported and responded, so it is present; only the version
    could not be determined.
    """
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox("no version here"))

    available, version = EnvironmentChecker.check_whitebox()

    assert available is True
    assert version == "unknown"


def test_whitebox_available_when_version_call_raises(monkeypatch):
    """
    A failing version call leaves the package reported as available.

    Version reporting is a convenience; failing to obtain it does not mean the
    tool is missing, and treating it that way would block processing that could
    have run.
    """

    class _BrokenVersion:
        def WhiteboxTools(self):
            class _Tools:
                def version(self_inner):
                    raise RuntimeError("binary not yet downloaded")

            return _Tools()

    _stub_module(monkeypatch, "whitebox", _BrokenVersion())

    available, version = EnvironmentChecker.check_whitebox()

    assert available is True
    assert version == "unknown"


def test_whitebox_unavailable_when_import_fails(monkeypatch):
    """A missing whitebox package is reported as unavailable."""
    _block_imports(monkeypatch, "whitebox")

    available, version = EnvironmentChecker.check_whitebox()

    assert available is False
    assert version is None


# ----------------------------------------------------------------------
# Aggregate report structure
# ----------------------------------------------------------------------


def test_report_is_an_environment_status():
    """get_environment_report returns the documented dataclass."""
    report = get_environment_report()

    assert isinstance(report, EnvironmentStatus)


def test_report_exposes_every_documented_field():
    """
    The report carries every field consumers read.

    SystemService and the processing gate both index into this structure, so a
    renamed or dropped field would break them at runtime.
    """
    report = get_environment_report()

    for field in (
        "python_version",
        "python_version_str",
        "gdal_python_available",
        "gdal_python_version",
        "gdal_cli_available",
        "gdal_cli_version",
        "rasterio_available",
        "rasterio_version",
        "pyproj_available",
        "pyproj_version",
        "whitebox_available",
        "whitebox_version",
        "all_available",
        "missing_tools",
    ):
        assert hasattr(report, field), f"report is missing '{field}'"


def test_report_field_types_are_stable():
    """Availability flags are booleans and missing_tools is a list."""
    report = get_environment_report()

    for flag in (
        report.gdal_python_available,
        report.gdal_cli_available,
        report.rasterio_available,
        report.pyproj_available,
        report.whitebox_available,
        report.all_available,
    ):
        assert isinstance(flag, bool)

    assert isinstance(report.missing_tools, list)
    assert all(isinstance(tool, str) for tool in report.missing_tools)


def test_report_is_deterministic_for_one_environment():
    """
    Two reports from an unchanged environment agree.

    A checker whose answer varied between calls would make the status endpoint
    unreliable.
    """
    first = get_environment_report()
    second = get_environment_report()

    assert first == second


def test_report_records_the_running_python_version():
    """Both Python version fields report the running interpreter."""
    report = get_environment_report()
    expected = EnvironmentChecker.get_python_version()

    assert report.python_version == expected
    assert report.python_version_str == expected


# ----------------------------------------------------------------------
# all_available and missing_tools
# ----------------------------------------------------------------------


def test_all_available_when_every_required_tool_is_present(monkeypatch):
    """
    A complete toolchain yields all_available with no missing tools.

    Every dependency the checker treats as required is stubbed present, so the
    result is the same regardless of what the host machine has.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    report = EnvironmentChecker.check_all()

    assert report.all_available is True
    assert report.missing_tools == []


def test_all_available_ignores_gdal_python_bindings(monkeypatch):
    """
    Absent GDAL Python bindings do not block availability.

    The pipeline shells out to gdalwarp and gdaldem rather than importing
    osgeo, so the bindings are reported but not required.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))
    _block_imports(monkeypatch, "osgeo")

    report = EnvironmentChecker.check_all()

    assert report.gdal_python_available is False
    assert report.all_available is True
    assert report.missing_tools == []


def test_all_available_ignores_pyproj(monkeypatch):
    """
    Absent pyproj does not block availability.

    pyproj is reported for visibility but is not in the required set, so its
    absence appears in the report without appearing in missing_tools.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))
    _block_imports(monkeypatch, "pyproj")

    report = EnvironmentChecker.check_all()

    assert report.pyproj_available is False
    assert report.all_available is True
    assert "pyproj" not in report.missing_tools


def test_missing_gdal_cli_blocks_availability(monkeypatch):
    """Absent GDAL command-line tools make the environment incomplete."""
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert any("GDAL" in tool for tool in report.missing_tools)


def test_missing_whitebox_blocks_availability(monkeypatch):
    """Absent WhiteboxTools makes the environment incomplete."""
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))
    _block_imports(monkeypatch, "whitebox")

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert "WhiteboxTools" in report.missing_tools


def test_missing_rasterio_blocks_availability(monkeypatch):
    """Absent rasterio makes the environment incomplete."""
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))
    _block_imports(monkeypatch, "rasterio")

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert "rasterio" in report.missing_tools


def test_missing_numpy_blocks_availability(monkeypatch):
    """
    Absent numpy makes the environment incomplete.

    numpy underpins every raster read and validation path, so the checker
    treats it as required rather than optional.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _block_imports(monkeypatch, "numpy")

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert report.numpy_available is False
    assert "numpy" in report.missing_tools


def test_missing_tools_lists_every_absent_requirement(monkeypatch):
    """
    An empty environment lists all four required tools, not just the first.

    An operator fixing one at a time would otherwise need four runs to discover
    what is needed.
    """
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))
    _block_imports(monkeypatch, "whitebox", "rasterio", "numpy", "pyproj", "osgeo")

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert len(report.missing_tools) == 4
    assert any("GDAL" in tool for tool in report.missing_tools)
    assert "WhiteboxTools" in report.missing_tools
    assert "rasterio" in report.missing_tools
    assert "numpy" in report.missing_tools


def test_missing_tools_empty_exactly_when_all_available(monkeypatch):
    """
    The two summary fields never disagree.

    all_available is derived from missing_tools, and a consumer reading either
    one must reach the same conclusion.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    complete = EnvironmentChecker.check_all()
    assert complete.all_available is (complete.missing_tools == [])

    _block_imports(monkeypatch, "whitebox")
    incomplete = EnvironmentChecker.check_all()
    assert incomplete.all_available is (incomplete.missing_tools == [])


def test_check_all_survives_a_fully_empty_environment(monkeypatch):
    """
    The checker completes with nothing installed.

    Reporting a missing dependency is its entire purpose, so it must not depend
    on any of them being present.
    """
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))
    _block_imports(
        monkeypatch, "osgeo", "whitebox", "rasterio", "pyproj", "numpy"
    )

    report = EnvironmentChecker.check_all()

    assert report.all_available is False
    assert report.gdal_cli_available is False
    assert report.gdal_python_available is False
    assert report.whitebox_available is False
    assert report.rasterio_available is False
    assert report.pyproj_available is False
    assert report.numpy_available is False
    assert report.python_version


def test_check_all_reports_stubbed_versions(monkeypatch):
    """Versions in the aggregate report come from the tools themselves."""
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox("WhiteboxTools v2.0.0"))
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "pyproj", type("pyproj", (), {"__version__": "3.6.1"}))
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    report = EnvironmentChecker.check_all()

    assert report.gdal_cli_version == "3.8.1"
    assert report.whitebox_version == "2.0.0"
    assert report.rasterio_version == "1.3.8"
    assert report.pyproj_version == "3.6.1"
    assert report.numpy_version == "1.26.2"


def test_version_is_none_for_an_absent_tool(monkeypatch):
    """
    An absent tool reports no version.

    A stale or invented version string would be worse than none: it would
    suggest a working installation.
    """
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))
    _block_imports(monkeypatch, "whitebox", "rasterio", "pyproj", "numpy", "osgeo")

    report = EnvironmentChecker.check_all()

    assert report.gdal_cli_version is None
    assert report.whitebox_version is None
    assert report.rasterio_version is None
    assert report.pyproj_version is None
    assert report.numpy_version is None


def test_get_environment_report_delegates_to_check_all(monkeypatch):
    """
    The public function returns what the checker produced.

    They must not diverge: callers use the function, while the tests above
    exercise the class.
    """
    sentinel = EnvironmentChecker.check_all()

    monkeypatch.setattr(EnvironmentChecker, "check_all", staticmethod(lambda: sentinel))

    assert get_environment_report() is sentinel


# ----------------------------------------------------------------------
# Report formatting
# ----------------------------------------------------------------------


def test_formatted_report_states_availability(monkeypatch):
    """A complete environment is described as such in the formatted report."""
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(monkeypatch, "whitebox", _FakeWhitebox())
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))

    text = format_environment_report(EnvironmentChecker.check_all())

    assert "ALL DEPENDENCIES MET" in text


def test_formatted_report_names_missing_tools(monkeypatch):
    """
    An incomplete environment lists what is absent.

    This text is what a CLI user sees, so it must name the tools rather than
    only report failure.
    """
    _raise_from_gdal_cli(monkeypatch, FileNotFoundError("gdalinfo"))
    _block_imports(monkeypatch, "whitebox", "rasterio", "numpy", "pyproj", "osgeo")

    text = format_environment_report(EnvironmentChecker.check_all())

    assert "MISSING DEPENDENCIES" in text
    assert "WhiteboxTools" in text
    assert "rasterio" in text


def test_formatted_report_includes_installation_guidance(monkeypatch):
    """
    The formatted report tells the reader how to install what is missing.

    A report naming a gap without a remedy leaves the reader to search for the
    package name themselves.
    """
    _stub_gdal_cli(monkeypatch, "GDAL 3.8.1")
    _stub_module(
        monkeypatch, "rasterio", type("rasterio", (), {"__version__": "1.3.8"})
    )
    _stub_module(monkeypatch, "numpy", type("numpy", (), {"__version__": "1.26.2"}))
    _block_imports(monkeypatch, "whitebox")

    text = format_environment_report(EnvironmentChecker.check_all())

    assert "pip install whitebox" in text


def test_formatted_report_includes_python_version():
    """The formatted report states the running Python version."""
    report = get_environment_report()

    text = format_environment_report(report)

    assert report.python_version_str in text


def test_formatted_report_is_multiline_text():
    """The formatted report is a readable multi-line string."""
    text = format_environment_report(get_environment_report())

    assert isinstance(text, str)
    assert len(text.splitlines()) > 5