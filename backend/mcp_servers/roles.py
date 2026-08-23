import os
import yaml
import sys
from mcp.server.fastmcp import FastMCP, Message

# Path to the roles directory
ROLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "roles")

mcp = FastMCP("contextus-roles", instructions="Provides dynamic roles and prompts from Contextus configurations")

def load_role(role_name: str) -> dict:
    file_path = os.path.join(ROLES_DIR, f"{role_name}.yaml")
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Note: FastMCP currently discovers prompts via decorators, 
# but to dynamically load from YAMLs, we would define them manually 
# or register them. FastMCP 1.28.0 allows dynamic prompts by using the low-level Server API,
# or we can just read the directory and create a generic 'get_role' prompt.

@mcp.prompt("get_role")
def get_role(role_name: str) -> list[Message]:
    """Retrieve the system instructions for a specific role."""
    role_data = load_role(role_name)
    if not role_data:
        return [Message.user(f"Role '{role_name}' not found.")]
        
    instruction = role_data.get("system_instruction", "")
    description = role_data.get("description", "")
    
    prompt_content = f"Role: {role_name}\nDescription: {description}\n\nSystem Instruction:\n{instruction}"
    
    return [Message.user(prompt_content)]

@mcp.tool()
async def list_available_roles() -> str:
    """Lists all available roles configured in the system."""
    if not os.path.exists(ROLES_DIR):
        return "No roles directory found."
        
    roles = []
    for filename in os.listdir(ROLES_DIR):
        if filename.endswith(".yaml"):
            roles.append(filename.replace(".yaml", ""))
            
    return f"Available roles: {', '.join(roles)}"

if __name__ == "__main__":
    mcp.run()
