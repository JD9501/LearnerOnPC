# Third-party software and models used by LearnerOnPC

| Component | Licence | How it is used |
|---|---|---|
| Qt 6 and Qt WebEngine, through PySide6 (The Qt Company) | LGPL v3 | The window and the web pages (mind map, graph, study pack). Included as separate, replaceable library files. Source: https://code.qt.io and https://download.qt.io/official_releases/QtForPython/. Qt WebEngine includes Chromium, with its own third-party notices inside the Qt WebEngine files. |
| llama.cpp `llama-server` (ggml-org) | MIT | Downloaded on first use of Ask from https://github.com/ggml-org/llama.cpp/releases; runs the AI model on this PC. |
| Qwen3 4B Instruct 2507, GGUF (Qwen team, Alibaba Cloud) | Apache 2.0 | Downloaded on first use of Ask from Hugging Face; answers questions. |
| huggingface_hub (Hugging Face) | Apache 2.0 | Downloads the model. |
| tqdm | MPL 2.0 and MIT | Download progress. |
| psutil | BSD 3-Clause | Reads the PC's memory; stops leftover server processes. |
| truststore | MIT | Uses Windows' own certificate store for downloads. |
| certifi | MPL 2.0 | Certificate bundle for downloads. |
| PyInstaller bootloader | GPL v2 with the bootloader exception | Starts the packaged app (the exception allows this use). |

The full licence texts are published by each project at the addresses above.
