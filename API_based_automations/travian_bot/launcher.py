import time
import random
import json
import os
import sys
import subprocess
import threading
from identity_handling.login import login
from core.travian_api import TravianAPI
from core.hero_manager import HeroManager
from core.database_helpers import load_latest_unoccupied_oases
from oasis_raiding_from_scan_list_main import run_raid_planner
from raid_list_main import run_one_farm_list_burst
from features.raiding.reset_raid_plan import reset_saved_raid_plan
from features.raiding.setup_interactive_plan import setup_interactive_raid_plan
from identity_handling.identity_manager import handle_identity_management
from identity_handling.identity_helper import load_villages_from_identity
from features.hero.hero_operations import run_hero_operations as run_hero_ops, print_hero_status_summary
from features.hero.hero_raiding_thread import run_hero_raiding_thread
from features.building.building_planner import create_or_update_building_plan, run_building_plan_once
from features.hero.adventure_operations import run_adventure_once
from features.hero.adventure_browser import run_adventure_browser_once
from features.strategy.advanced_loop import (
    ensure_strategy_files,
    run_advanced_strategy_cycle,
    run_advanced_strategy_loop,
    set_single_building_priority,
    set_pause_development_and_train_mode,
)
from features.reports.village_intelligence_report import run_village_intelligence_report
from features.defense.defense_timing_planner import run_defense_timing_planner

# === CONFIG ===
WAIT_BETWEEN_CYCLES_MINUTES = 34
JITTER_MINUTES = 5
SERVER_SELECTION = 0  # 👈 update if needed

def view_identity():
    """Display the current identity information."""
    try:
        with open("database/identity.json", "r", encoding="utf-8") as f:
            identity = json.load(f)
        
        travian_identity = identity.get("travian_identity", {})
        faction = travian_identity.get("faction", "unknown").title()
        tribe_id = travian_identity.get("tribe_id", "unknown")
        
        print("\n👤 Current Identity:")
        print(f"Faction: {faction} (ID: {tribe_id})")
        print("\n🏰 Villages:")
        
        for server in travian_identity.get("servers", []):
            for village in server.get("villages", []):
                name = village.get("village_name", "Unknown")
                vid = village.get("village_id", "?")
                x = village.get("x", "?")
                y = village.get("y", "?")
                print(f"- {name} (ID: {vid}) at ({x}|{y})")
    
    except FileNotFoundError:
        print("\n❌ No identity file found. Please set up your identity first.")
    except json.JSONDecodeError:
        print("\n❌ Identity file is corrupted. Please set up your identity again.")
    except Exception as e:
        print(f"\n❌ Error reading identity: {e}")

def update_village_coordinates():
    """Update coordinates for existing villages."""
    try:
        # Read current identity
        with open("database/identity.json", "r", encoding="utf-8") as f:
            identity = json.load(f)
        
        travian_identity = identity.get("travian_identity", {})
        servers = travian_identity.get("servers", [])
        
        if not servers:
            print("\n❌ No servers found in identity file.")
            return
        
        # For each server's villages
        for server in servers:
            villages = server.get("villages", [])
            print("\n🏰 Your villages:")
            for i, village in enumerate(villages):
                name = village.get("village_name", "Unknown")
                current_x = village.get("x", "?")
                current_y = village.get("y", "?")
                print(f"[{i}] {name} - Current coordinates: ({current_x}|{current_y})")
            
            while True:
                try:
                    choice = input("\nEnter village number to update (or 'q' to quit): ").strip()
                    if choice.lower() == 'q':
                        break
                    
                    village_idx = int(choice)
                    if village_idx < 0 or village_idx >= len(villages):
                        print("❌ Invalid village number.")
                        continue
                    
                    coords = input(f"Enter new coordinates for {villages[village_idx]['village_name']} (format: x y): ").strip().split()
                    if len(coords) != 2:
                        print("❌ Invalid format. Please enter two numbers separated by space.")
                        continue
                    
                    x, y = map(int, coords)
                    villages[village_idx]["x"] = x
                    villages[village_idx]["y"] = y
                    print(f"✅ Updated coordinates to ({x}|{y})")
                
                except ValueError:
                    print("❌ Invalid input. Please enter valid numbers.")
                except Exception as e:
                    print(f"❌ Error: {e}")
        
        # Save updated identity
        with open("database/identity.json", "w", encoding="utf-8") as f:
            json.dump(identity, f, indent=4, ensure_ascii=False)
        print("\n✅ Successfully saved updated coordinates.")
    
    except FileNotFoundError:
        print("\n❌ No identity file found. Please set up your identity first.")
    except Exception as e:
        print(f"\n❌ Error: {e}")

def handle_identity_management():
    """Handle identity management sub-menu."""
    print("""
👤 Identity Management
[1] Set up new identity
[2] View current identity
[3] Update village coordinates
[4] Back to main menu
""")
    choice = input("Select an option: ").strip()
    
    if choice == "1":
        print("\nℹ️ Running identity setup...")
        setup_script = os.path.join(os.path.dirname(__file__), "setup_identity.py")
        subprocess.run([sys.executable, setup_script], check=False)
    elif choice == "2":
        view_identity()
    elif choice == "3":
        update_village_coordinates()
    elif choice == "4":
        return
    else:
        print("❌ Invalid choice.")

def run_hero_operations(api: TravianAPI):
    """Run hero-specific operations including checking status and sending to suitable oases."""
    run_hero_ops(api)

def setup_interactive_raid_plan(api, server_url):
    """Set up a raid plan interactively."""
    print("\n🎯 Interactive Raid Plan Creator")
    print("[1] Set up new raid plan")
    print("[2] Use saved configuration")
    
    choice = input("\nSelect an option: ").strip()
    
    if choice == "1":
        from features.raiding.setup_interactive_plan import setup_interactive_raid_plan
        setup_interactive_raid_plan(api, server_url)
    elif choice == "2":
        # Load saved configuration
        try:
            with open("database/saved_raid_plan.json", "r", encoding="utf-8") as f:
                saved_config = json.load(f)
            
            # Create raid plans for all villages
            from features.raiding.setup_interactive_plan import create_raid_plan_from_saved
            from identity_handling.identity_helper import load_villages_from_identity
            
            villages = load_villages_from_identity()
            if not villages:
                print("❌ No villages found in identity. Exiting.")
                return
            
            for i, village in enumerate(villages):
                print(f"\nSetting up raid plan for {village['village_name']}...")
                create_raid_plan_from_saved(api, server_url, i, saved_config)
            
            print("\n✅ Finished setting up raid plans for all villages.")
        except FileNotFoundError:
            print("❌ No saved raid plan found. Please set up a new raid plan first.")
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("❌ Invalid option.")

def run_map_scan(api: TravianAPI):
    """Run map scanning operations."""
    print("\n🗺️ Map Scanning")
    print("[1] Scan for unoccupied oases")
    print("[2] View latest scan results")
    print("[3] Back to main menu")
    
    choice = input("\nSelect an option: ").strip()
    
    if choice == "1":
        from features.map_scanning.scan_map import scan_map_for_oases
        print("\n🔍 Starting map scan...")
        scan_map_for_oases(api)
        print("✅ Map scan complete!")
    elif choice == "2":
        from core.database_helpers import load_latest_unoccupied_oases
        villages = load_villages_from_identity()
        if not villages:
            print("❌ No villages found in identity. Exiting.")
            return
        
        print("\nAvailable villages:")
        for idx, v in enumerate(villages):
            print(f"[{idx}] {v['village_name']} at ({v['x']}, {v['y']})")
        
        try:
            village_idx = int(input("\nSelect village to view oases for: ").strip())
            selected_village = villages[village_idx]
            oases = load_latest_unoccupied_oases(f"({selected_village['x']}_{selected_village['y']})")
            
            if not oases:
                print("❌ No oases found in latest scan.")
                return
            
            print(f"\n📊 Found {len(oases)} unoccupied oases near {selected_village['village_name']}:")
            for coord_key, oasis_data in oases.items():
                x_str, y_str = coord_key.split("_")
                print(f"- Oasis at ({x_str}, {y_str})")
        except (ValueError, IndexError):
            print("❌ Invalid village selection.")
    elif choice == "3":
        return
    else:
        print("❌ Invalid choice.")

def main():
    print("\n" + "="*40)
    print("🎮 TRAVIAN AUTOMATION LAUNCHER")
    print("="*40)
    
    print("\n🌾 FARM LIST:")
    print("1) Farm burst")
    print("2) Configure farm lists")
    print("3) Run farm from config")
    
    print("\n🏰 OASIS RAID:")
    print("4) Setup raid plan")
    print("5) Reset raid plan")
    print("6) Test raid (single village)")
    
    print("\n🤖 AUTOMATION:")
    print("7) 👑 FULL AUTO MODE 👑")
    print("   • Farm lists + Oasis raids")
    print("   • Multi-village loop")
    
    print("\n🗺️ MAP SCANNING:")
    print("8) Scan & View Oases")
    
    print("\n👤 ACCOUNT:")
    print("9) Hero Operations")
    print("10) Identity & Villages")
    print("11) Test Hero Raiding Thread (Standalone)")
    print("12) Building Plan (setup/run)")
    print("13) Hero Adventure (run once)")
    print("14) Advanced Strategy Loop (eco + raid + build)")
    print("15) Village Intelligence Report (for ChatGPT planning)")
    print("16) Single Priority Override (quick)")
    print("17) Defense Timing Planner (between incoming waves)")
    
    print("\n" + "="*40)

    choice = input("\n👉 Select an option: ").strip()

    # Login first
    print("\n🔐 Logging into Travian...")
    session, server_url = login()
    api = TravianAPI(session, server_url)

    if choice == "1":
        run_one_farm_list_burst(api)
    elif choice == "2":
        from features.farm_lists.manage_farm_lists import update_farm_lists
        update_farm_lists(api, server_url)
    elif choice == "3":
        from features.farm_lists.farm_list_raider import run_farm_list_raids
        villages = load_villages_from_identity()
        if not villages:
            print("❌ No villages found in identity. Exiting.")
            return
        for village in villages:
            run_farm_list_raids(api, server_url, village["village_id"])
    elif choice == "4":
        setup_interactive_raid_plan(api, server_url)
    elif choice == "5":
        reset_saved_raid_plan()
    elif choice == "6":
        print("\n🎯 Starting single-village oasis raiding (testing mode)...")
        run_raid_planner(api, server_url, multi_village=False, run_farm_lists=False)
    elif choice == "7":
        print("\n🤖 Starting full automation mode...")
        # Ask for delay
        while True:
            try:
                delay_input = input("\nWould you like to delay the start? (y/N): ").strip().lower()
                if delay_input == 'y':
                    delay_minutes = float(input("Enter delay in minutes (supports decimals): "))
                    if delay_minutes > 0:
                        print(f"\n⏳ Waiting {delay_minutes} minutes before starting...")
                        time.sleep(delay_minutes * 60)
                        break
                    else:
                        print("Delay must be greater than 0 minutes.")
                else:
                    print("\nStarting immediately...")
                    break
            except ValueError:
                print("Please enter a valid number of minutes.")

        # Ask for full-auto cycle settings
        wait_between_cycles_minutes = WAIT_BETWEEN_CYCLES_MINUTES
        jitter_minutes = JITTER_MINUTES
        custom_timing_input = input(
            f"\nUse custom cycle timing? (default {WAIT_BETWEEN_CYCLES_MINUTES} min ± {JITTER_MINUTES}) (y/N): "
        ).strip().lower()
        if custom_timing_input == "y":
            while True:
                try:
                    wait_between_cycles_minutes = float(input("Enter base wait between cycles in minutes: ").strip())
                    jitter_minutes = float(input("Enter jitter in minutes (0 for none): ").strip())
                    if wait_between_cycles_minutes <= 0 or jitter_minutes < 0:
                        print("Values must be: base wait > 0 and jitter >= 0.")
                        continue
                    break
                except ValueError:
                    print("Please enter valid numbers.")

        # Ask if user wants to skip farm lists on first run
        skip_farm_lists_first_run = False
        skip_input = input("\nDo you want to skip farm lists on the first run? (y/N): ").strip().lower()
        if skip_input == 'y':
            skip_farm_lists_first_run = True

        print("\n[Main] Starting hero raiding thread...")
        # Start hero raiding thread
        hero_thread = threading.Thread(target=run_hero_raiding_thread, args=(api,))
        hero_thread.daemon = True  # Exits with main program
        hero_thread.start()
        print("[Main] Hero raiding thread started successfully.")

        first_cycle = True
        while True:
            try:
                print(f"\n[Main] Starting cycle at {time.strftime('%H:%M:%S')}")
                print("[Main] Running raid planner...")
                # Skip farm lists only on the first run if requested
                if first_cycle and skip_farm_lists_first_run:
                    run_raid_planner(api, server_url, reuse_saved=True, multi_village=True, run_farm_lists=False)
                else:
                    run_raid_planner(api, server_url, reuse_saved=True, multi_village=True, run_farm_lists=True)
                first_cycle = False
                
                # Print hero status summary at the end of the cycle
                print("[Main] Fetching hero status summary...")
                hero_manager = HeroManager(api)
                status = hero_manager.fetch_hero_status()
                if status:
                    print_hero_status_summary(status)
                else:
                    print("❌ Could not fetch hero status summary.")
                
                # Calculate next cycle time with jitter
                jitter = random.uniform(-jitter_minutes, jitter_minutes)
                total_wait_minutes = wait_between_cycles_minutes + jitter
                total_wait_minutes = max(0.1, total_wait_minutes)
                print(f"[Main] Cycle complete. Waiting {total_wait_minutes} minutes...")
                time.sleep(total_wait_minutes * 60)
            except Exception as e:
                print(f"[Main] ⚠️ Error during cycle: {e}")
                print("[Main] 🔁 Attempting re-login and retry...")
                time.sleep(3)
                session, server_url = login()
                api = TravianAPI(session, server_url)
                print("[Main] ✅ Re-login successful.")
    elif choice == "8":
        run_map_scan(api)
    elif choice == "9":
        run_hero_operations(api)
    elif choice == "10":
        handle_identity_management()
    elif choice == "11":
        print("\n🦸 Testing Hero Raiding Thread (Standalone)...")
        run_hero_raiding_thread(api)
    elif choice == "12":
        print("\n🏗️ Building Plan")
        print("[1] Setup / update building plan")
        print("[2] Run building plan once")
        sub = input("Select an option: ").strip()
        if sub == "1":
            create_or_update_building_plan(api)
        elif sub == "2":
            run_building_plan_once(api)
        else:
            print("❌ Invalid choice.")
    elif choice == "13":
        print("\nHero Adventure")
        print("[1] API attempt (current method)")
        print("[2] Browser explore (recommended)")
        print("[3] Browser watch video then explore")
        sub = input("Select an option: ").strip()
        if sub == "1":
            run_adventure_once(api)
        elif sub == "2":
            run_adventure_browser_once(
                server_url=server_url,
                watch_video_first=False,
                headless=False,
                session_cookies=api.session.cookies.get_dict(),
            )
        elif sub == "3":
            run_adventure_browser_once(
                server_url=server_url,
                watch_video_first=True,
                headless=False,
                session_cookies=api.session.cookies.get_dict(),
            )
        else:
            print("Invalid choice.")
    elif choice == "14":
        print("\nAdvanced Strategy Loop")
        print("[1] Generate / update strategy files (JSON + CSV + XLSX)")
        print("[2] Run one advanced strategy cycle")
        print("[3] Run advanced strategy loop")
        sub = input("Select an option: ").strip()
        if sub == "1":
            cfg, csv_file, xlsx_file = ensure_strategy_files()
            print("Strategy files ready:")
            print(f"- {cfg}")
            print(f"- {csv_file}")
            print(f"- {xlsx_file}")
        elif sub == "2":
            ensure_strategy_files()
            run_advanced_strategy_cycle(api, server_url)
        elif sub == "3":
            ensure_strategy_files()
            max_cycles_input = input("Max cycles (blank = infinite): ").strip()
            max_cycles = None
            if max_cycles_input:
                try:
                    max_cycles = int(max_cycles_input)
                    if max_cycles <= 0:
                        print("Invalid value: max cycles must be a positive integer.")
                        return
                except ValueError:
                    print("Invalid max cycles value.")
                    return
            run_advanced_strategy_loop(api, server_url, max_cycles=max_cycles)
        else:
            print("Invalid choice.")
    elif choice == "15":
        print("\nVillage Intelligence Report")
        print("Analyzing all villages for eco, troops, queue, buildings, and strategy context...")
        try:
            json_file, md_file, prompt_file = run_village_intelligence_report(api, server_url)
            print("Report files generated:")
            print(f"- {json_file}")
            print(f"- {md_file}")
            print(f"- {prompt_file}")
        except Exception as e:
            print(f"Failed to generate intelligence report: {e}")
    elif choice == "16":
        print("\nSingle Priority Override")
        print("[1] Palace only in village '1' (recommended now)")
        print("[2] Palace only in a specific village key")
        print("[3] Custom single building priority")
        print("[4] Stop development + train settlers/troops now")
        sub = input("Select an option: ").strip()

        if sub == "1":
            target_in = input("Target level for palace priority (default 20): ").strip()
            target_level = int(target_in) if target_in else 20
            cfg = set_single_building_priority(
                village_selector="1",
                contains_any=["palace", "residence"],
                target_level=target_level,
                replace_existing_phases=True,
            )
            print(f"✅ Applied palace-only priority to village key '1' at target level {target_level}.")
            print(f"Strategy file updated: {cfg}")
            print("ℹ️ Manual building plan override has been disabled so this priority is used directly.")
        elif sub == "2":
            village_key = input("Village key (example: 1, 2, *, or exact village name): ").strip()
            if not village_key:
                print("❌ Village key cannot be empty.")
                return
            target_in = input("Target level for palace priority (default 20): ").strip()
            target_level = int(target_in) if target_in else 20
            cfg = set_single_building_priority(
                village_selector=village_key,
                contains_any=["palace", "residence"],
                target_level=target_level,
                replace_existing_phases=True,
            )
            print(f"✅ Applied palace-only priority to village key '{village_key}' at target level {target_level}.")
            print(f"Strategy file updated: {cfg}")
            print("ℹ️ Manual building plan override has been disabled so this priority is used directly.")
        elif sub == "3":
            village_key = input("Village key (example: 1, 2, *, or exact village name): ").strip()
            if not village_key:
                print("❌ Village key cannot be empty.")
                return
            contains_raw = input("Building keywords (comma-separated, e.g. warehouse,entrepot): ").strip()
            keywords = [x.strip() for x in contains_raw.split(",") if x.strip()]
            if not keywords:
                print("❌ At least one keyword is required.")
                return
            target_in = input("Target level (default 10): ").strip()
            target_level = int(target_in) if target_in else 10
            cfg = set_single_building_priority(
                village_selector=village_key,
                contains_any=keywords,
                target_level=target_level,
                replace_existing_phases=True,
            )
            print(f"✅ Applied single-priority override for '{keywords}' on village key '{village_key}'.")
            print(f"Strategy file updated: {cfg}")
            print("ℹ️ Manual building plan override has been disabled so this priority is used directly.")
        elif sub == "4":
            attempts_in = input("Training actions per village each cycle (default 1): ").strip()
            attempts = int(attempts_in) if attempts_in else 1
            interval_in = input("Training interval minutes (default 10): ").strip()
            interval_minutes = float(interval_in) if interval_in else 10.0
            pause_targets_in = input(
                "Pause building for which villages? (comma-separated keys/ids, e.g. 1 or 1,2; blank = all): "
            ).strip()
            pause_targets = [x.strip() for x in pause_targets_in.split(",") if x.strip()] if pause_targets_in else []
            cfg = set_pause_development_and_train_mode(
                enabled=True,
                settlers_first=True,
                troop_training=True,
                attempts_per_village=attempts,
                training_interval_minutes=interval_minutes,
                settler_amount=1,
                troop_amount="max",
                pause_village_selectors=pause_targets,
            )
            if pause_targets:
                print(
                    "✅ Selective building pause enabled for villages "
                    f"{pause_targets}. Training mode enabled (settlers first, then troops at MAX possible each attempt)."
                )
            else:
                print(
                    "✅ Development paused for all villages. Training mode enabled "
                    "(settlers first, then troops at MAX possible each attempt)."
                )
            print(f"Strategy file updated: {cfg}")
            print("ℹ️ Run option 14 to execute this mode in cycles.")
        else:
            print("Invalid choice.")
    elif choice == "17":
        run_defense_timing_planner()
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    main()
