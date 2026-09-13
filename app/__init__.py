from dotenv import load_dotenv

# Export .env into os.environ before any HuggingFace import reads HF_HOME at import time.
load_dotenv()
