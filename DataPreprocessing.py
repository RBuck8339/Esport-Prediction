# Likely important stats (pregame)
'''
By Player:
- Average Kills
- Average Deaths
- Average Assists
- Average Denies
- Average Creep Score
- Average Wards
- Total Moneys
- Total gold spent
- Average Gold Per Minute
- Average Tower Damage
- Win/Loss Ratio 
- Stuns
- Damage
- Healing

For team:
- Team Composition
- First Blood Rate
- Average Roshan Kills
- Average tower kills
- Average Barracks kills
- Average Match Duration
- Early/Late game win rates
- Expected Heros

By Hero:
- Hero scalability
- Hero impact
- Hero winrate (By patch)
'''

# Two problems
'''
One: Predict match outcome only knowing pre match info (no heros) Binary Classification
Two: Predict how the match will play out knowing players and the draft Regression
'''


from asyncio.windows_events import NULL
import requests
import json
import sqlite3
import pandas as pd
import numpy as np

import time # Just so that we don't go over allowed calls per minute


OPEN_DOTA_URL = f'https://api.opendota.com/api/'

STRATZ_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJTdWJqZWN0IjoiZTg0ZjQ3ZDMtMjViZC00MWFjLTk5MDEtODc4M2U1OTg1ZjY2IiwiU3RlYW1JZCI6IjExNTUxNTk3OTIiLCJuYmYiOjE3MTY4NzEzODEsImV4cCI6MTc0ODQwNzM4MSwiaWF0IjoxNzE2ODcxMzgxLCJpc3MiOiJodHRwczovL2FwaS5zdHJhdHouY29tIn0.V9os4YLxMhMI7f5PFZgObBJsoMrLUkmKjv2DxN4SvOg'
STRATZ_GRAPHQL = 'https://api.stratz.com/graphiql/'

class DataPreprocesser():
    def __init__(self, connection, cursor):
        # For the SQL database
        self.connection = connection
        self.cursor = cursor

        # For data for the model
        self.data = pd.DataFrame()
        self.matches = pd.DataFrame()
        self.players = pd.DataFrame()
        self.player_stats_match = pd.DataFrame()

    
    # Request data from the Open Dota REST API
    def request_data_OpenDota(self, source, params):
        # Default, most querys have no parameters
        if params == None:
            response = requests.get(source) 

        # If we have parameters to query with
        else:
            response = requests.get(source, params=params)

        if response.status_code == 200:
            return response.json()
        
        # If we have hit our request limit, update the database
        elif response.status_code == 429:
            self.to_database()

        else:
            print(f"Error: {response.status_code}")
            return None


    # Request data from Stratz GraphQl application
    def request_data_Stratz(self, params, type):
        # Info necessary to query
        url = 'https://api.stratz.com/graphql'
        headers = {
            
            'Authorization': f'Bearer {STRATZ_TOKEN}',
            'Content-Type': 'application/json'
        }

        if type == "Match":
            # Excessive amount of info for now
            query = """
            query GetMatchDetails($matchId: Long!) {
                match(id: $matchId) {
                    id
                    didRadiantWin
                    durationSeconds
                    towerStatusRadiant
                    towerStatusDire
                    barracksStatusRadiant
                    barracksStatusDire
                    gameMode
                    radiantKills
                    direKills
                    gameVersionId
                    firstBloodTime
                    players {
                        steamAccountId
                        heroId
                        position
                        numDenies
                        numLastHits
                        position
                        kills
                        deaths
                        assists
                        networth
                        goldPerMinute
                        experiencePerMinute
                        heroDamage
                        towerDamage
                        heroHealing
                        isRadiant
                        imp
                    }
                }
            }
            """
            variables = {
                "matchId": int(params['matchId'])
            }

        elif type == 'PlayerInfo':
            query = """
            query getPlayerDetails($steamAccountId: Long!, $position: [MatchPlayerPositionType]!) {
                player(steamAccountId: $steamAccountId) {
                    winCount
                    matchCount
                    rank
                    matches(request: {isParsed: true, positionIds: $position, lobbyTypeIds: 7, take: 50}) {
                        id
                        didRadiantWin
                        players(steamAccountId: $steamAccountId) {
                            steamAccountId
                            isRadiant
                            position
                            kills
                            deaths
                            assists
                            networth
                            goldPerMinute
                            gold
                            numLastHits
                            numDenies
                            experiencePerMinute
                            towerDamage
                            heroDamage
                            heroHealing
                            isVictory
                            leaverStatus
                            imp
                            stats {
                                campStack
                                wards {
                                    type
                                }
                                wardDestruction {
                                    isWard
                                }
                            }
                        }
                    }
                }
            }
            """

            variables = {
                "steamAccountId": int(params['steamAccountId']),
                "position": params['position']
            }

        response = requests.post(url, json={'query': query, 'variables': variables}, headers=headers)
        print(response.content)

        if response.status_code == 200:
            return response.json()
        
        # If we have hit our request limit, update the database
        elif response.status_code == 429:
            self.to_database()
            return None

        else:
            print(f"Error fetching match details from Stratz: {response.status_code}")
            return None

    # Calculate player stats
    # Thinking of adding: 
    #   Not yet, but eventually winrate after X minutes
    #   Not yet, but eventually most played heros (one hot encoding)
    # Is set up to use Stratz, I'd rather not change
    def process_player_info(self, players):
        players = []  # Since it may be needed for anonymous player calculations

        for player in players:
            player_stats = {}  # Init/Reset dict

            player_id = player['account_id']
            position = player['position']

            if player['isRadiant'] == True:
                curr_team_radiant = 1
            else:
                curr_team_radiant = 0

            recent_matches = self.request_data_Stratz(params={'steamAccountId': player_id, 'position': position}, type = "PlayerInfo")

            recent_wl, recent_leaver, curr_team_wl = [], [], []  # Just counts, no real computations
            kdas, kills, deaths, assists, networth, gpm, exp_pm = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])  # To speed up computations
            cs_score, denies, tower_damage, hero_damage, hero_healing, vision, camp_stacks, imp = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])    # To speed up computations

            # Compute on the current role
            if len(recent_matches) < 20:
                main_kdas, main_kills, main_deaths, main_assists, main_networth, main_gpm, main_exp_pm = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])  # To speed up computations
                main_cs_score, main_denies, main_tower_damage, main_hero_damage, main_hero_healing, main_camp_stacks, main_imp = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])  # To speed up computations

                for curr_match in recent_matches['matches']['players']:

                    # If the player hasnt died, don't divide by 0
                    if curr_match['deaths'] > 0:
                        np.append(main_kdas, ((curr_match['kills'] + curr_match['assits']) / curr_match['deaths']))
                    else:
                        np.append(main_kdas, (curr_match['kills'] + curr_match['assists']))

                    # Add player stats to array
                    np.append(main_kills, curr_match['kills'])
                    np.append(main_deaths, curr_match['deaths'])
                    np.append(main_assists, curr_match['assists'])
                    np.append(main_networth, curr_match['gold'])
                    np.append(main_gpm, curr_match['goldPerMinute'])
                    np.append(main_exp_pm, curr_match['experiencePerMinute'])
                    np.append(main_cs_score, curr_match['numLastHits'])
                    np.append(main_denies, curr_match['numDenies'])
                    np.append(main_tower_damage, curr_match['towerDamage'])
                    np.append(main_hero_damage, curr_match['heroDamage'])
                    np.append(main_hero_healing, curr_match['heroHealing'])
                    np.append(main_camp_stacks, curr_match['stats']['campStack'][-1])
                    np.append(main_imp, curr_match['imp'])

                params = {'steamAccountId': player_id, 'position': ["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]}  # Use all positions this time
                recent_matches = self.request_data_Stratz(params=params, type = "PlayerInfo")  # Run query again across all matches regardless of role

            # Find statistics from last 50 matches
            for curr_match in recent_matches['matches']['players']:
                vision_count = np.array()

                # Find if the player won and which team they were on when they won
                if curr_match['isVictory'] == True:
                    recent_wl.append(1)
                    if(curr_team_radiant == 1 and curr_match['isRadiant'] == True):
                        curr_team_wl.append(1)
                    elif(curr_team_radiant == 0 and curr_match['isRadiant'] == False):
                        curr_team_wl.append(1)
                    else:
                        curr_team_wl.append(2)
                else:
                    recent_wl.append(0)
                    if(curr_team_radiant == 1 and curr_match['isRadiant'] == True):
                        curr_team_wl.append(0)
                    elif(curr_team_radiant == 0 and curr_match['isRadiant'] == False):
                        curr_team_wl.append(0)
                    else:
                        curr_team_wl.append(2)

                # Find players vision contribution
                for ward in curr_match['wards']:
                    np.append(vision_count, ward['type'])
                for ward in curr_match['wardDestruction']:
                    # Don't count summonable units that provide vision
                    if(ward['isWard'] == True):
                        np.append(vision_count, 1)

                # If the player hasnt died, don't divide by 0
                if curr_match['deaths'] > 0:
                    np.append(((curr_match['kills'] + curr_match['assits']) / curr_match['deaths']))
                else:
                    np.append(kdas, (curr_match['kills'] + curr_match['assists']))

                # Add player stats to array
                np.append(kills, curr_match['kills'])
                np.append(deaths, curr_match['deaths'])
                np.append(assists, curr_match['assists'])
                np.append(networth, curr_match['gold'])
                np.append(gpm, curr_match['goldPerMinute'])
                np.append(exp_pm, curr_match['experiencePerMinute'])
                np.append(cs_score, curr_match['numLastHits'])
                np.append(denies, curr_match['numDenies'])
                np.append(tower_damage, curr_match['towerDamage'])
                np.append(hero_damage, curr_match['heroDamage'])
                np.append(hero_healing, curr_match['heroHealing'])
                np.append(vision, vision_count.size)
                np.append(camp_stacks, curr_match['stats']['campStack'][-1])
                np.append(imp, curr_match['imp'])
                
            # Easily accessible stats
            player_stats['account_id'] = player_id
            player_stats['win_rate'] = player['winCount'] / player['matchCount']  # Calculate Lifetime win/loss percent
            player_stats['rank'] = player['rank']  # Find player rank in current match

            # Since we don't have enough matches to just rely off of main
            if len(recent_matches) < 20:
                # Calculate stats and add to dict
                player_stats['average_kda'] = self.supplementary_matches_calc(np.mean(main_kdas), len(main_kdas), np.mean(kdas), len(kdas))
                player_stats['average_kills'] = self.supplementary_matches_calc(np.mean(main_kills), len(main_kills), np.mean(kills), len(kills))
                player_stats['average_deaths'] = self.supplementary_matches_calc(np.mean(main_deaths), len(main_deaths), np.mean(deaths), len(deaths))
                player_stats['average_assists'] = self.supplementary_matches_calc(np.mean(main_assists), len(main_assists), np.mean(assists), len(assists))
                player_stats['average_cs'] = self.supplementary_matches_calc(np.mean(main_cs_score), len(main_cs_score), np.mean(cs_score), len(cs_score))
                player_stats['average_denies'] = self.supplementary_matches_calc(np.mean(main_denies), len(main_denies), np.mean(denies), len(denies))
                player_stats['average_networth'] = self.supplementary_matches_calc(np.mean(main_networth), len(main_networth), np.mean(networth), len(networth))
                player_stats['average_gold_per_minute'] = self.supplementary_matches_calc(np.mean(main_gpm), len(main_gpm), np.mean(gpm), len(gpm))
                player_stats['average_exp_per_minute'] = self.supplementary_matches_calc(np.mean(main_exp_pm), len(main_exp_pm), np.mean(exp_pm), len(exp_pm))
                player_stats['average_tower_damage'] = self.supplementary_matches_calc(np.mean(main_tower_damage), len(main_kdas), np.mean(tower_damage), len(tower_damage))
                player_stats['average_hero_damage'] = self.supplementary_matches_calc(np.mean(main_hero_damage), len(main_hero_damage), np.mean(hero_damage), len(hero_damage))
                player_stats['average_hero_healing'] = self.supplementary_matches_calc(np.mean(main_hero_healing), len(main_hero_healing), np.mean(hero_healing), len(hero_healing))
                player_stats['average_camps_stacked'] = self.supplementary_matches_calc(np.mean(main_camp_stacks), len(main_camp_stacks), np.mean(camp_stacks), len(camp_stacks))
                player_stats['average_individual_match_performance'] = self.supplementary_matches_calc(np.mean(main_imp), len(main_imp), np.mean(imp), len(imp))
                
                # More reflective on all games
                player_stats['average_vision_participation'] = np.mean(vision)
                player_stats['recent_win_rate'] = (recent_wl.count(1) / len(recent_wl)) 
                player_stats['recent_times_left'] = (recent_leaver.count(1) / len(recent_leaver))
                player_stats['curr_team_wl_rate'] = curr_team_wl.count(1) / (curr_team_wl.count(1) + curr_team_wl.count(0))


            else:
                 # Calculate stats and add to dict
                player_stats['average_kda'] = np.mean(kdas)
                player_stats['average_kills'] = np.mean(kills)
                player_stats['average_deaths'] = np.mean(deaths)
                player_stats['average_assists'] = np.mean(assists)
                player_stats['average_cs'] = np.mean(cs_score)
                player_stats['average_denies'] = np.mean(denies)
                player_stats['average_networth'] = np.mean(networth)
                player_stats['average_gold_per_minute'] = np.mean(gpm)
                player_stats['average_exp_per_minute'] = np.mean(exp_pm)
                player_stats['average_tower_damage'] = np.mean(tower_damage)
                player_stats['average_hero_damage'] = np.mean(hero_damage)
                player_stats['average_hero_healing'] = np.mean(hero_healing)
                player_stats['average_camps_stacked'] = np.mean(camp_stacks)
                player_stats['average_individual_match_performance'] = np.mean(imp)
                player_stats['average_vision_participation'] = np.mean(vision)
                player_stats['recent_win_rate'] = (recent_wl.count(1) / len(recent_wl)) 
                player_stats['recent_times_left'] = (recent_leaver.count(1) / len(recent_leaver))
                player_stats['curr_team_wl_rate'] = curr_team_wl.count(1) / (curr_team_wl.count(1) + curr_team_wl.count(0))

            temp_df = pd.DataFrame(player_stats)
            print(temp_df)
            self.players = pd.concat([self.players, temp_df], ignore_index=True)
           

    # Calculations when there are not enough matches on desired position
    def supplementary_matches_calc(self, main_stat, num_main, supp_stat, num_supp):
        # Average for main role we want to analyze times number of matches + overall performance times overall matches divided by total matches
        value = ((main_stat * num_main) + (supp_stat * num_supp)) / 50
        return value

    # If a player is appearing anonymous
    def process_anon_player(self, players, anon_players, match):
        kdas, kills, deaths, assists, networth, gpm, exp_pm = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])  # To speed up computations
        cs_score, denies, tower_damage, hero_damage, hero_healing, vision, camp_stacks, imp = np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), np.array([])    # To speed up computations

        # Find the average stats of known players in the match
        for player in players:
            np.append(kdas, player['average_kda'])
            np.append(kills, player['average_kills'])
            np.append(deaths, player['average_deaths'])
            np.append(assists, player['average_assists'])
            np.append(gpm, player['average_cs'])
            np.append(exp_pm, player['average_denies'])
            np.append(networth, player['average_networth'])
            np.append(cs_score, player['average_gold_per_minute'])
            np.append(denies, player['average_exp_per_minute'])
            np.append(tower_damage, player['average_tower_damage'])
            np.append(hero_damage, player['average_hero_damage'])
            np.append(hero_healing, player['average_hero_healing'])
            np.append(vision, player['average_vision_participation'])
            np.append(camp_stacks, player['average_camps_stacked'])
            np.append(imp, player['average_individual_match_performance'])

        for player in anon_players:
            player_stats = {}
            player_stats['account_id'] = np.NaN  # Since we don't have an ID for this player
            player_stats['match_id'] = match['match_id']  # So we know which match this anonymous player belongs to
            player_stats['win_rate']= 0.50  # Nice middle ratio since unknown
            player_stats['rank'] = match['average_rank'] # Let's find the average rank of their team and plug that in
            player_stats['account_id'] = np.NaN
            player_stats['win_rate'] = 0.50
            player_stats['rank'] = player['rank']  # Find player rank in current match
            player_stats['average_kda'] = np.mean(kdas)
            player_stats['average_kills'] = np.mean(kills)
            player_stats['average_deaths'] = np.mean(deaths)
            player_stats['average_assists'] = np.mean(assists)
            player_stats['average_cs'] = np.mean(cs_score)
            player_stats['average_denies'] = np.mean(denies)
            player_stats['average_networth'] = np.mean(networth)
            player_stats['average_gold_per_minute'] = np.mean(gpm)
            player_stats['average_exp_per_minute'] = np.mean(exp_pm)
            player_stats['average_tower_damage'] = np.mean(tower_damage)
            player_stats['average_hero_damage'] = np.mean(hero_damage)
            player_stats['average_hero_healing'] = np.mean(hero_healing)
            player_stats['average_camps_stacked'] = np.mean(camp_stacks)
            player_stats['average_individual_match_performance'] = np.mean(imp)
            player_stats['average_vision_participation'] = np.mean(vision)
            player_stats['recent_win_rate'] = 0.50
            player_stats['recent_times_left'] = 0
            player_stats['curr_team_wl_rate'] = 0.50

            temp_df = pd.DataFrame(player_stats)
            print(temp_df)
            self.players = pd.concat([self.players, temp_df], ignore_index=True)


    # Find all players previous match statistics
    def process_players(self, match):
        print(match)
        players = []
        anon_players = []

        # For each player in the match, get their ID or note there isn't one
        for player in match['players']:
            if player.get('steamAccountId') is not None:
                players.append(player)

            # Should append role, prob a dict
            else:
                print("Found anonymous player")  # Since Stratz might not respect hidden profiles
                anon_players.append(player)
                
        self.process_player_info(players, match)
        self.process_anon_player(players, anon_players, match)
            

    # Generate a set of new matches and find info about the players
    def match_info(self):
        new_matches = self.request_data_OpenDota(OPEN_DOTA_URL + '/publicMatches', params={"min_rank": 70}) # A list of 100 matches

        print(new_matches)        

        # Process each match individually
        for match in new_matches:
            print(match['match_id'])
            # If this is not a ranked match, don't analyze it
            if match['lobby_type'] != 7:
                continue

            curr_match = self.request_data_OpenDota(OPEN_DOTA_URL + '/matches/' + match['match_id'], None)

            curr_match, players = self.clean_match(curr_match)

            curr_match['averageRank'] = match['avg_rank_tier']

            # Prepare players for future analysis (not current task)
            players_to_add = []
            for player in players:
                player['matchId'] = curr_match['id']

                players_to_add.append(player)

                print(player['position'])

                # Add heros to match data for later analysis
                if player['isRadiant'] == True:
                    curr_match['Radiant_' + player['position'] + '_hero'] = player['heroId']
                    curr_match['Radiant_' + player['position'] + 'id'] = player['steamAccountId']
                elif player['isRadiant'] == False:
                    curr_match['Dire_' + player['position'] + '_hero'] = player['heroId']
            
            curr_match['radiantKills'] = np.array(curr_match['radiantKills']).sum()
            curr_match['direKills'] = np.array(curr_match['direKills']).sum()

            # Add to the dataframes
            temp_dict = curr_match
            temp_dict = temp_dict.pop('players')
            temp_df = pd.DataFrame(temp_dict)
            print(temp_df)
            self.player_stats_match = pd.concat([temp_df, self.player_stats_match], ignore_index=True)
            temp_match_df = pd.DataFrame([curr_match])
            print(temp_match_df)
            self.matches = pd.concat([temp_match_df, self.matches], ignore_index=True)

            self.process_players(curr_match)


    # Remove unwanted keys from the dict
    def clean_match(self, match):
        keys = ['match_id', 'barracks_status_dire', 'barracks_status_radiant', 'dire_score', 'duration', 'first_blood_time', 'game_mode', 'league_id',
                'match_seq_num, radiant_gold_adv', 'radiant_score', 'radiant_xp_adv', 'radiant_win', 'tower_status_dire', 'tower_status_radiant', 'version', 'series_id', 'patch']


        new_match = {key: match[key] for key in keys if key in match}

                    # Get endgame advantage +/-
        new_match['radiant_gold_adv'] = new_match['radiant_gold_adv'][-1]
        new_match['radiant_xp_adv'] = new_match['radiant_xp_adv'][-1]
        match.pop('chat')
        match.pop('cluster')
        match.pop('cosmetics')
        match.pop('draft_timings')
        match.pop('engine')
        match.pop('human_players')
        match.pop('negative_votes')
        match.pop('objectives')
        match.pop('picks_bans')
        match.pop('positive_votes')
        match.pop('start_time')
        match.pop('teamfights')
        match.pop('replay_salt')
        match.pop('series_id')
        match.pop('series_type')
        match.pop('radiant_team')
        match.pop('dire_team')
        match.pop('league')
        match.pop('skill')
        match.pop('version')
        match.pop('all_word_counts')
        match.pop('my_word_counts')
        match.pop('throw')
        match.pop('comeback')
        match.pop('loss')
        match.pop('win')
        match.pop('replay_url')
        players = match['players']
        match.pop('players')

        return match, players 
    

    # Keeps the keys that we want to analyze, can edit
    def clean_player(self, player):
        # player.pop('damage')  # THINK ABOUT IT
        # player.pop('damage_taken')  # THINK ABOUT IT
        # player.pop('rune_pickups')  # THINK ABOUT IT

        keys = ['match_id', 'player_slot', 'account_id', 'assists', 'camps_stacked', 'deaths', 'denies', 'gold', 'gold_perm_min', 'hero_damage', 'hero_healing'
                'hero_id', 'kills', 'lane_pos', 'last_hits', 'leaver_status', 'obs_placed', 'sen_placed', 'tower_damaged', 'xp_per_min', 'isRadiant', 'total_gold', 'kda', 'rank_tier']
        
        new_player = {key: player[key] for key in keys if key in player}
        return new_player


    # Add to the database of players and matches
    def to_database(self):
        self.players.to_sql("Players", self.connection, if_exists='append', index=False)
        self.matches.to_sql("Matches", self.connection, if_exists='append', index=False)


    # If the database exists and has enough records
    def to_dataframes(self):
        self.players = pd.read_sql_query("SELECT * FROM Players", self.connection)
        self.matches = pd.read_sql_query("SELECT * FROM Matches", self.connection)

    
    def clean(self):
        self.players = self.players.drop_duplicates()
        self.matches = self.matches.drop_duplicates()

    
    # Merge Data into the format that I need and return it
    def merge_data(self):
        pass