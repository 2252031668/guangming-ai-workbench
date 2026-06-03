# Repository Notes For Codex

- When reading Chinese text in Windows PowerShell 5.1, do not assume mojibake means the file is corrupted. First switch the current terminal to UTF-8:

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

- This is especially important before inspecting or rewriting Chinese Markdown files such as `README.md`.
