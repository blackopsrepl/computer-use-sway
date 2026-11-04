#!/bin/sh
# double-fork launcher: the GUI process is a grandchild, its parent exits
( /usr/bin/python3 "$@" & ) 
exit 0
