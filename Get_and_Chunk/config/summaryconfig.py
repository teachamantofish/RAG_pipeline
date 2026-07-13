# on each run, do the following:
# 1. Check if LLM access is configured; e.g.: is the Ollama server running?
# 2. Verify small chunk handling settings
# 3. Verify global summary settings
# 4. Test on 1-N chunks first

CHUNK_SUMMARY_MODEL = "qwen3-next-80b-a3b-instruct-hmt"
TESTINGMODE = None                      # If None, run the full pipeline; if a number, limit to N chunks for faster testing.

# Execution behavior
SUMMARY_MAX_WORKERS = 4                 # Concurrent LLM requests for chunk summaries (1 = serial).
RESUME_SUMMARIES = True                 # Skip chunks that already have a non-empty chunk_summary (safe re-runs).
SUMMARY_FLUSH_EVERY = 50                # Persist progress to a_chunks.json after every N summarized chunks.

# Chunk-level summary
ENABLE_CHUNK_SUMMARY = True             # Set to False to skip summary generation during testing
CHUNK_SUMMARY_SIZE = 100                # Max tokens for chunk summary
CHUNK_SUMMARY_PROMPT_TEMPLATE = "Read the parent heading '{heading_context}' and use that context to summarize the current chunk in {size} tokens or less. Consider how this chunk relates to its parent and provide conceptual insights rather than reworded terminology. If the content is short, provide a higher-level abstraction. Focus on functionality and purpose. Active voice. Be concise and clear."
CHUNK_SUMMARY_TEMPERATURE = 1           # Temperature for OpenAI API calls. Other parameters can be added as needed. Lower values make the output more deterministic.

# Small chunk handling
SKIP_CHUNK_THRESHOLD = 50               # Do not summarize.
PAD_CHUNK_THRESHOLD = 150               # Specifies whether to add the concat_header_path and summaries to the chunk content.
ADD_CONCAT_HEADER_PATH = True           # Whether to add the concat_header_path to the chunk content for small chunks
ADD_CHUNK_SUMMARY = True                # Whether to add the chunk summary to the chunk content for small chunks
ADD_PAGE_SUMMARY = False                # Whether to add the page summary to the chunk content for small chunks

# File/ or H1 summary
ENABLE_FILE_SUMMARY = False             # Disable file and page-level (Heading 1) summaries to avoid adding content that competes with granular chunk content.
FILE_SUMMARY_MODEL = "qwen3-next-80b-a3b-instruct-hmt"
FILE_SUMMARY_SIZE = 125                 # Max tokens for page summary
FILE_SUMMARY_PROMPT = (f"Summarize the main ideas in this page or heading in {FILE_SUMMARY_SIZE} tokens. Do not include heading text, bullets, quotes. Identify the functionality and purpose of child nodes. Active voice. Be concise and clear.")
FILE_SUMMARY_TEMPERATURE = 1            # Lower values make the output more deterministic.

# Summarize Summaries
ENABLE_SUMMARY_SUMMARY = False          # Set to False to skip summary generation during testing
CHILD_COUNT = 2                         # Threshold for which we create a summary of sibling summaries. 
SUMMARY_SUMMARY_MODEL = "qwen3-next-80b-a3b-instruct-hmt"
SUMMARY_SUMMARY_SIZE = 125              # Max tokens for summary of summaries
SUMMARY_SUMMARY_PROMPT = (f"Summarize the main ideas in this summary of summaries in {SUMMARY_SUMMARY_SIZE} tokens. Do not include heading text, bullets, quotes. Identify the functionality and purpose of this related content. Active voice. Be concise and clear.")
SUMMARY_SUMMARY_TEMPERATURE = 1         # Temperature for OpenAI API calls. Other parameters can be added as needed. Lower values make the output more deterministic.

# Code-level summary
ENABLE_CODE_SUMMARY = False             # Unused. Set to False to skip code summary generation during testing
CODE_SUMMARY_MODEL = "qwen3-next-80b-a3b-instruct-hmt"
CODE_SUMMARY_SIZE = 40                  # Max tokens for code summary
CODE_SUMMARY_PROMPT = (f"Summarize this code chunk's purpose in {CODE_SUMMARY_SIZE} tokens: focus on functionality. Active voice. Be concise and clear."
)
CODE_SUMMARY_TEMPERATURE = 0.2          # Temperature for OpenAI API calls. Other parameters can be added as needed. Lower values make the output more deterministic.
