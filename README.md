
## About

Wad2Pic is a python program that draws an isometric image of a Doom map from a WAD file. Check the root directory for a sample for the E1M1 map for the first Doom, famous "Hangar". Do you want an image like this for your favorite Doom map? You can do it, wad2pic is (relatively) easy to use, even if you don't know a thing about Python. In this file let me outline how one can go about it.

## Prerequisites

You need to have 3 things installed: python itself and a couple of libraries. Let's look at the details:

## Console commands

To be able to install modules and run the python script you need to be able to launch and operate something that is called a “Command prompt”, be able to navigate to the folder with your WAD files and run commands mentioned in this Readme file.

In Windows Command prompt can be reached with “Win+R” and then running “cmd” command. On Mac, the app called “Terminal” is what you need. And if you are using Linux I am guessing you know more about using command prompt than I ever will

### Python

If you use a Unix-based machine, chances are Python is already preinstalled. Good for you. I think Mac has Python, but old version 2 of it, so you gonna have to install a new one. Windows does not come with Python, so you'll have to have it installed too.
Go to https://www.python.org/downloads/, pick the installer for your OS and install it.
(depending on your system and if it has python2, Python3 will be installed either as python3, or as python - just make sure you know which one is the right one.)

All recent versions of Python3 come with a tool to install libraries, called PIP. It can be activated from your command line as "pip" or "pip3" - the same difference as for the python and python3 commands.

### PIL Library

PIL is the most popular Python library for working with pictures. To install it, run a console command:

	pip install pillow


### Numpy library

You may have heard about this one as the one that those data-scientists and AI-engineers are using.  Yep, that's exactly the one. It has some great functions to efficiently work with large arrays of data, and this is exactly what wad2pic uses it for. To install it, run the command:

	pip install numpy

Now you should be all set to generate your first map image.
### PyPNG library

Some of the WADs use PNG as the format to store images. PyPNG provides some useful tools to work with those. Here’s the command:

	pip install pypng

## Simplest way to use the program

Make sure you have following files in your folder:
* wad2pic.py
* iWAD file. In most cases it is either DOO2.WAD or doom.wad
* pWAD file. That's your custom level file. Let's say you have one called myawesomemap.wad and the map you are interested in is MAP01

All you need to do is to run this command in console (note that in your case it may be “python” instead of “python3”):

	python3 wad2pic.py DOOM2.WAD MAP01 myawesomemap.wad

All parameters are required and they go exactly in this order: iWAD, map name, pWAD.
If everything is done correctly, the program starts to print various log messages such as drawing a blueprint, that flats and then walls. After a few seconds/minutes/hours it should be done. The time needed depends on the size of the map. For example, "Hangar" takes about a minute on a reasonably powerful computer.

## More advance usage

Wad2Pican accepts a few parameter that can modify the way image is generated, such as:
* Map rotation angle
* How far high objects are shifted to the side
* How squeezed is the map vertically? 
Second and third parameters can produce different types of isometric projections: Military projection, Dimetric Orthogonal projection, maybe some others too.

There are a few other parameters, for example support of Hexen-style format for linedefs, that ZDoom sometimes uses. If you see the resulting image that resembles an angry sea urchin - that may be the cause.

To use these parameters, you do need to know a bit of Python - although not a lot. Basically, how to create a dictionary and pass it to a function - pretty basic stuff. Example of how it can be done is in the comments of wad2pic.py

## Known limitations

While Wad2Pic should work fine for most WADs there are a few things that would trip it over. Here's the list:
* Very big maps. It can take on a map that ends up a 20kx20k, image, maybe even 25kx25k (This is achieved on a computer with 16Gb RAM). Maps bigger than that, especially with large sectors in them, may deplete memory and crash. Generally, the program is a bit unoptimized, can take a lot of time/memory, so be patient.
* PK3 format is not supported (yet)
* Maps with HOMs and very thin 1-pixel sectors can be a problem. Likely the program will not crash, but such sectors will be left untextured.
* Texture-as-Flat and Flat-as-Texture are not supported
* Hexen and Heretic WAD formats are not supported
* Other fancy stuff modern source ports may have, if it is not in the original WAD spec.


Let me know if you ran into issues (gamescomputersplay@gmail.com), we'll try to figure it out.

Hope you will find this little utility useful. Some resulting files do look really cool, if I do say so myself.
