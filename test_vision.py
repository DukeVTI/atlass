import asyncio
import os
import base64
from anthropic import AsyncAnthropic

async def main():
    with open('test_pillow2.png', 'rb') as f:
        b64_data = base64.b64encode(f.read()).decode('utf-8')
        
    client = AsyncAnthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    msg = await client.messages.create(
        model='claude-3-5-haiku-20241022',
        max_tokens=200,
        messages=[
            {
                'role': 'user', 
                'content': [
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': b64_data}}, 
                    {'type': 'text', 'text': 'What do you see in this screenshot? Describe it.'}
                ]
            }
        ]
    )
    print(msg.content[0].text)

if __name__ == '__main__':
    asyncio.run(main())
