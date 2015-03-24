#!/bin/bash

# lcm tunnel
${DRC_BASE}/software/tools/tools/scripts/atlas_restart_tunnel.sh server
ssh atlas0 -t 'bash -ic "\${DRC_BASE}/software/tools/tools/scripts/atlas_restart_tunnel.sh"'

# lcm bridges
ssh atlas0 -t 'bash -ic "\${DRC_BASE}/software/tools/tools/scripts/atlas_restart_bridge.sh"'
ssh atlas1 -t 'bash -ic "\${DRC_BASE}/software/tools/tools/scripts/atlas_restart_bridge.sh"'
ssh atlas2 -t 'bash -ic "\${DRC_BASE}/software/tools/tools/scripts/atlas_restart_bridge.sh"'

# network shaper
killall -q drc-network-shaper
screen -D -m -S shaper-link3 drc-network-shaper -r robot -c drc_robot.cfg  -i link3 &
screen -D -m -S shaper-link2 drc-network-shaper -r robot -c drc_robot.cfg  -i link2 &
