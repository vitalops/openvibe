import streamlit as st
from loopgpt.agent import Agent

def custom_agent_cli(agent, continuous=False, user_input=None):
    agent_cmd = agent.cli(continuous=continuous)
    while agent_cmd["status"] == "awaiting_input":
        if user_input is not None:
            agent_cmd = agent.continue_cli(user_input)
            user_input = None
        else:
            break
    return agent_cmd

st.title("LoopGPT Agent")

# Create an instance of the LoopGPT Agent
model = st.selectbox(
    "Select the GPT model to use:",
    ("gpt-3.5-turbo", "gpt-4")
)
agent = Agent(model=model)

# Set the attributes of the Agent
name = st.text_input("Agent name", "DevGPT")
description = st.text_area("Agent description", "an AI assistant that write complete programs in any coding languages")
goals = st.text_area("Agent goals", "- A Windows app/widget is being developed to display an overlay of clocks for sleep cycles. The average sleep cycle lasts about 90 minutes, and it is recommended to have four to six cycles of sleep every 24 hours for optimal rest and rejuvenation. The sleep cycle app/widget for Windows will provide users with an easy-to-use tool to track and visualize their sleep cycles, helping them achieve better sleep and overall well-being. The user will input their desired wakeup time, and the clock will show optimal times to go to bed, with the nearest bed time to the current time is highlighted in red. Write the necessary code in C# and also provide a clean a modern design in xaml format so I can just paste both in Micorsoft Visual Studio 2022")

response_key = st.empty()

# Execute the Agent's CLI step by step
execute_steps = False
if st.button("Step through Agent CLI"):
    execute_steps = True
# You can exit the CLI by typing "exit".
if st.button("Stop execution"):
    execute_steps = False
    st.write("Agent CLI execution stopped.")


continuous_execution = st.checkbox("Run continuously")

if execute_steps:
    # Update the agent's attributes
    agent.name = name
    agent.description = description
    agent.goals = goals.splitlines()

    st.write("Starting Agent CLI...")
    agent_cmd = custom_agent_cli(agent, continuous=continuous_execution, user_input=None)
    while agent_cmd["status"] == "awaiting_input":
        st.write(agent_cmd["prompt"])

        if "Execute? (Y/N/Y:n to execute n steps continuously):" in agent_cmd["prompt"]:
            user_input = response_key.text_input("Enter your response (Y, N, or Y:n):")
        else:
            user_input = response_key.text_input("Enter your response:")

        agent_cmd = agent.continue_cli(user_input)

        if agent_cmd["status"] == "success":
            st.write("Agent CLI successfully executed!")
            break
        elif agent_cmd["status"] == "error":
            st.error(agent_cmd["message"])
            break
        response_key.empty()
else:
    st.write("Click the 'Step through Agent CLI' button to execute the Agent's CLI step by step.")
