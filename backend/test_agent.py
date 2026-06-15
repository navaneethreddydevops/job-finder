import asyncio
from agent import run_job_finder_agent

async def log(msg):
    print(msg, end="")

async def main():
    import uuid
    try:
        res = await run_job_finder_agent("C2C Data Engineer", log, session_id=str(uuid.uuid4()))
        print("\nSuccess:", res)
    except Exception as e:
        print("\nError:", e)

if __name__ == "__main__":
    asyncio.run(main())
