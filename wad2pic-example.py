from wad2pic import wad2pic 

# These are settings for dimetric orthogonal projection
# use no options for miilitary
options = {
    # Margins around the map
    "margins": 300,
    # Gamma correction of the final map
    # .7 is to lighten it up a little, 1 to bypass
    "gamma"  : .7,
    # X and Y size of a wall (in relation to actual height)
    "coefX"  : 0,
    "coefY"  : .8,
    # rotate, degrees clockwise. 0 - no rotation
    "rotate": 30,
    # scale alongY axis, to create isometric view
    # 1 for no scaling
    "scaleY": .8,
    #"zStyle": True,
    "debug" : False,
    "verbose": True
    }

# Quiick test with Classic Hangar
wad2pic("doom.wad", "E1M1", options=options)



