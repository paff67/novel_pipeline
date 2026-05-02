$env:PYTHONPATH = 'D:\card\novel_pipeline\src;D:\card\novel_pipeline\.venv\Lib\site-packages'
& 'C:\Users\paff\AppData\Local\Programs\Python\Python314\python.exe' -m novel_pipeline_stable serve-monitor --data-root 'D:\card\novel_pipeline\data' --host 127.0.0.1 --port 8765
exit $LASTEXITCODE
