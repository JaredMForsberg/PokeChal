# PokeChal

An AI agent developed for the Pokémon TCG AI Battle Challenge hosted by Kaggle and The Pokémon Company. The goal of this project is to build an autonomous agent capable of making strong strategic decisions in Pokémon Trading Card Game battles under conditions of hidden information and uncertainty.

Overview

This repository contains my implementation, experiments, and training pipeline for the competition.

Current objectives include:

Building a competitive Pokémon TCG battle agent
Experimenting with different decision-making algorithms
Evaluating strategies against benchmark opponents
Improving consistency through automated self-play and analysis
Project Structure
.
├── src/                # Source code
├── models/             # Saved models/checkpoints
├── configs/            # Configuration files
├── notebooks/          # Research and experimentation
├── logs/               # Training and evaluation logs
├── submissions/        # Competition submissions
└── README.md
Approach

This project is currently exploring:

Rule-based decision making
State evaluation heuristics
Search/planning algorithms
Reinforcement learning
Self-play training
Game state feature engineering

As development progresses, this section will be updated with the final architecture and methodology.

Getting Started
Clone the repository
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
Install dependencies
pip install -r requirements.txt
Run the agent
python main.py
Experiments
Experiment	Description	Status
Baseline Agent	Initial implementation	✅
Heuristic Improvements	Better board evaluation	🚧
Self-Play Training	Agent vs. itself	🚧
Reinforcement Learning	Policy optimization	Planned
Results

Competition performance will be tracked here.

Version	Notes
v0.1	Initial submission
v0.2	Improved evaluation function
v0.3	Self-play enhancements
Future Work
Improve long-term planning
Better handling of hidden information
Optimize search performance
Hyperparameter tuning
Ensemble strategies
Extensive evaluation against top-performing agents
Competition

This project was created for the Pokémon TCG AI Battle Challenge on Kaggle, where participants develop AI agents capable of competing in Pokémon Trading Card Game matches using competition-specific rules and a restricted card pool.

License

This project is released under the MIT License.
