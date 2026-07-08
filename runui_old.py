import os
import streamlit as st
from loopgpt.agent import Agent

st.set_page_config(page_title="LoopGPT UI", page_icon=":robot_face:", layout="wide")

st.title("LoopGPT UI")

st.write("Welcome to the LoopGPT UI! Your AI assistant is here to help you research and find the best tech products.")

# Sidebar inputs
st.sidebar.title("Agent Configuration")
model_choice = st.sidebar.selectbox("Select the model:", ["gpt-3.5-turbo", "gpt-4"])
agent_name = st.sidebar.text_input("Agent Name:", value="ResearchGPT")
agent_description = st.sidebar.text_input("Agent Description:", value="an AI assistant that researches and finds the best tech products")
goal_input = st.sidebar.text_area("Agent Goals (one per line):", value="Search for the best headphones on Google\nAnalyze specs, prices and reviews to find the top 5 best headphones\nWrite the list of the top 5 best headphones and their prices to a file\nSummarize the pros and cons of each headphone and write it to a different file called 'summary.txt'")

# Set up the LoopGPT Agent
agent = Agent(model=model_choice)
agent.name = agent_name
agent.description = agent_description
agent.goals = [goal.strip() for goal in goal_input.split("\n")]

# Display agent information
st.sidebar.write("**Current Agent Information**")
st.sidebar.write(f"**Name:** {agent.name}")
st.sidebar.write(f"**Description:** {agent.description}")
st.sidebar.write("**Goals:**")
for goal in agent.goals:
    st.sidebar.write(f"- {goal}")

# Main input and interaction loop
conversation_history = []
user_input = st.text_input("Type your question or command here:")

if user_input:
    conversation_history.append(("User", user_input))

start_agent_button = st.button("Start Agent")

def simulate_agent_cli(agent, user_input):
    response = agent.ask(user_input)
    return response

if start_agent_button and conversation_history:
    user_input = conversation_history[-1][1]
    response = simulate_agent_cli(agent, user_input)
    conversation_history.append((agent.name, response))
    st.write(f"{agent.name}: {response}")

st.write("### Conversation History")
for speaker, message in conversation_history:
    st.write(f"{speaker}: {message}")
