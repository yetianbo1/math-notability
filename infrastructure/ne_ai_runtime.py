"""Load the optional CPU runtime before Qt initializes Windows DLL paths."""
runtime=None
load_error=None
try:
    import onnxruntime as runtime
    runtime.disable_telemetry_events()
except Exception as exc:
    load_error=exc
