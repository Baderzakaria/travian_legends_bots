# Travian Advanced Strategy (Bot-Oriented)

This strategy is encoded in `database/strategy/advanced_strategy.json` and executed by launcher option `14`.

## Phase order used by the bot

1. `phase_1_cranny_safety`
- Upgrade all detected Crannies to level 10 first.
- Goal: reduce raid losses, especially before/around beginner protection end.

2. `phase_2_resource_push`
- Upgrade all resource fields (slots 1-18) to level 6.
- Then push Main Building, Warehouse, and Granary to level 10.
- Goal: stable eco and storage so queues keep running.

3. `phase_3_bootstrap_military`
- Upgrade Rally Point, Barracks, Stable, Academy to minimum working levels.
- Goal: unlock consistent troop training and raiding infrastructure.

4. `phase_4_raid_defend_balance`
- Push resources toward level 10 and maintain growth.
- Build military + wall + storage for sustained offense/defense.

## Why this ordering

- Cranny first: official guidance recommends building/upgrading Cranny before beginner protection ends; level benchmarks strongly affect raid safety.
- Economy before heavy army: official ROI guidance favors investments that pay back quickly (fields/storage) before overspending on army too early.
- Defense still matters for raiders: wall type and level significantly influence defensive outcomes.
- Farm Lists are high-impact automation once available.

## Official references used

- Hiding Resources & Cranny: https://support.travian.com/en/support/solutions/articles/7000068298
- Walls and Rams: https://support.travian.com/en/support/solutions/articles/7000065986-walls-and-rams
- Farm Lists: https://support.travian.com/en/support/solutions/articles/7000064015-farm-lists
- Balancing Military and Economy: https://support.travian.com/en/support/solutions/articles/7000092524-balancing-military-and-economy
- Early Development ROI: https://support.travian.com/en/support/solutions/articles/7000091814-early-development-return-on-investment-roi-
- Maximizing Efficiency: https://support.travian.com/en/support/solutions/articles/7000092519-maximizing-efficiency-managing-your-account
