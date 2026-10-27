# Pyrogram string session banane ke liye (apne PC/Termux par):
#   pip install pyrofork tgcrypto
#   python gen_session.py
import asyncio
from pyrogram import Client


async def main():
    api_id = int(input("API_ID: "))
    api_hash = input("API_HASH: ").strip()
    async with Client("gen", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        print("\nYour STRING SESSION (isko kisi ko mat dena):\n")
        print(await app.export_session_string())


asyncio.run(main())
