import asyncio
from backend.agent import run_job_finder_agent

async def log_callback(msg):
    print(msg, end="", flush=True)

async def main():
    try:
        import uuid
        res = await run_job_finder_agent("C2C Data Engineer", log_callback=log_callback, session_id=str(uuid.uuid4()))
        print("\n\nFINAL PARSED RESULT:")
        print(res)
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    asyncio.run(main())
