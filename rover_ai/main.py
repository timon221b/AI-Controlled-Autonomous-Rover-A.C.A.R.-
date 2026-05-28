import asyncio
from orchestrator import RoverOrchestrator

if __name__ == "__main__":
    rover = RoverOrchestrator()
    import time
    time.sleep(2)          # give ESP-NOW a moment
    rover.esp.send_resume()
    asyncio.run(rover.run())

