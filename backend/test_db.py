import sys
import asyncio

# Ensure we use the correct path to the installed packages
sys.path.insert(0, 'C:\\Users\\ahmed\\AppData\\Local\\Packages\\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\\LocalCache\\local-packages\\Python311\\site-packages')

import asyncpg

async def main():
    print("Testing 127.0.0.1...")
    try:
        conn = await asyncpg.connect('postgresql://postgres:Ady4026@127.0.0.1:5432/aicalling')
        print('✅ CONNECTED TO 127.0.0.1 successfully')
        await conn.close()
    except Exception as e:
        print('❌ FAILED 127.0.0.1:', type(e).__name__, str(e))

    print("\nTesting localhost...")
    try:
        conn = await asyncpg.connect('postgresql://postgres:Ady4026@localhost:5432/aicalling')
        print('✅ CONNECTED TO localhost successfully')
        await conn.close()
    except Exception as e:
        print('❌ FAILED localhost:', type(e).__name__, str(e))

if __name__ == '__main__':
    asyncio.run(main())
