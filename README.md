# LearnerOnPC (demo)

LearnerOnPC is the earlier, view-only predecessor of **LearnLens**, a study companion that runs entirely on your PC.
It turns learning videos into notes, a mind map, a 3D knowledge graph and a study pack, and answers questions about
what you watched.

This demo gives an idea of what the app can do. It comes with three sessions that were already made, and it
answers questions about them with one AI model running on your own PC. It cannot record or analyse anything new:
those buttons explain that this is a demo.

## What you can try

| Tab | What it shows |
|---|---|
| Notes | Notes per part of each video, with "Definitions to review" and "Dropped and changed" (what the checks removed or changed, and why) |
| Mind map | The session's topics as a map you can fold and unfold |
| 3D graph | Concepts and how the video connects them, in 3D |
| Study pack | TL;DR, summary, takeaways, flashcards, quiz, teach-back, common mistakes, practice tasks, glossary |
| Ask | Questions answered from the sessions' notes, with the session and time each answer rests on |
| Master graph, Library | All sessions together: shared concepts, statistics |

**Why some things are blurred:** the demo sessions were recorded from videos made by other people. Their spoken
words (the transcripts) and screen pictures belong to those creators, so the demo shows them blurred, and any note
that repeated a video word for word is hidden.

## Run it (Windows 10/11, 64-bit)

1. Download `LearnerOnPC-windows.zip` from the [Releases](../../releases) page.
2. Unzip it anywhere and double-click `LearnerOnPC\LearnerOnPC.exe`.
3. The first question on the Ask tab offers a one-time download of the AI model: Qwen3 4B Instruct 2507 (about
   2.5 GB, Apache 2.0) and the llama.cpp server program (about 25 MB, MIT). After that Ask works offline.

Ask needs about 8 GB of memory. Everything else works on any PC. Nothing you type leaves your PC.

## Build it yourself

With Python 3.10-3.12 (64-bit) installed, double-click `Build-LearnerOnPC.bat`. It creates a Python environment, runs
the tests, builds the app folder and packs `LearnerOnPC-windows.zip`. The log is `build_log.txt`.

To run from source: `pip install -r requirements.txt`, then `python main.py`. Tests: `python tests/test_demo.py`.

## Licence

Copyright (c) 2026 Jithin Dev S. All rights reserved. The source is published so the demo can be reviewed and built;
no licence to copy, change or redistribute it is granted. Third-party software and models keep their own licences:
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
