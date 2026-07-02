import json
import random

from cg.game import battle_start, battle_finish, battle_select, visualize_data

def random_agent(obs_dict: dict) -> list[int]:
    return random.sample(list(range(len(obs_dict["select"]["option"]))), obs_dict["select"]["maxCount"])

sample_deck = [721,721,722,722,722,722,723,723,723,723,1092,1121,1121,1145,1145,1163,1163,1219,1219,1219,1219,1227,1227,1227,1227,1262,1262,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3]

obs_dict, _ = battle_start(sample_deck, sample_deck)
obs_log = [""]
action_log = [None]
while True:
    if obs_dict["current"]["result"] >= 0:
        break
    action = random_agent(obs_dict)
    obs_dict.pop("search_begin_input")
    obs_log.append(obs_dict)
    action_log.append(action)
    obs_dict = battle_select(action)

vis = json.loads(visualize_data())
for i in range(len(vis)):
    vis[i]["obs"] = obs_log[i]
    vis[i]["action"] = [action_log[i], action_log[i]]
with open("vis.json", "w") as file:
    json.dump(vis, file)

battle_finish()