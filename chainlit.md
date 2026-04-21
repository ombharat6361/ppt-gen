# LEXI Research Assistant

Ask questions about the documents in your **Sources/** folder.

Answers are grounded strictly in your indexed sources — I will tell you explicitly if something isn't covered.

---

**First time setup:**
```bash
pip install -r requirements.txt
python indexer.py      # scrapes + indexes all Sources
chainlit run app.py    # starts the chatbot
```

**Re-index after adding new files to Sources/:**
```bash
python indexer.py
```
