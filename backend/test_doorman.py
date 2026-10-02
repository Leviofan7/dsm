import asyncio
from agent.roles_impl.base_agent import BaseAgent
from agent.llm_manager import LLMManager

async def test():
    llm = LLMManager()
    await llm.initialize()
    
    # Mock model
    class MockModel:
        name = "deepseek-chat"
        provider_type = "openai"
        model_id = "deepseek-chat"
        api_key = "test"
        base_url = "test"
        
    class MockLLMManager(LLMManager):
        async def _chat(self, model, messages, tools):
            return {
                "choices": [{
                    "message": {
                        "content": "Уточняющий вопрос от Дурмана: какой фреймворк?"
                    }
                }]
            }
            
    agent = BaseAgent(MockLLMManager(), {}, MockModel(), [])
    async for e in agent.run_stream("Сделай логин", []):
        print("EVENT:", repr(e))

asyncio.run(test())
