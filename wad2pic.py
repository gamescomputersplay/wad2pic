"""wad2pic
Generates an isometric image of a Doom level.

Usage:
  wad2pic <iwad> <map> [<pwad>] [options]
  wad2pic (-h | --license | --version)

Options:
  -h --help                 Show this help text.
  --version                 Print current program version.
  --license                 Print the license.
  -m SIZE, --margin=SIZE    Pad the output image with SIZE margins.
                            [default: 300]
  -g AMT, --gamma=AMT       Gamma correct the final map with AMT.
                            Amounts < 1 will lighten the image 
                            and > 1 will darken.
                            [default: 0.7]
  -cx K, --coefx=K          X scaling of walls (relative to actual width).
                            [default: 0]
  -cy K, --coefy=K          Y scaling of walls (relative to actual height).
                            [default: .8]
  -r DEG, --rotate=DEG      Rotate DEGREES clockwise. 0 for no rotation.
                            [default: 30]
  --iso FACTOR              Adjust the isometric Y scale by FACTOR,
                            usually in the range of [0.5 - 0.9].
                            Set to 1 for no scaling.
                            [default: 0.8]
  --zstyle                  Use zDoom new linedef format 
                            (similar to Hexen format).
  --quiet                   Supress detailed messages during generation.
  --debug                   Print program stack trace in case of error.
"""

# WAD 2 PIC
# by GamesComputersPlay
######################################
# Program that draws an isometric DOOM level map from a WAD file,
# with textures, monsters, objects - everything

# Works with iWADs and pWADs (except maps that require multiple pWADs)
# Can fail if WAD has errors or customized in some very non-standard way
# Otherwise, seems to be working fine for all classic WADs and 90% of
# "Top 100 WADs of all time"

# See end of the file for usage example


# Known problems and missing features:
######################################
# - Things sticking out of some walls, if the thing is tall enough and the
#   wall is immediately to the right
#   More accurate zBuffer calculation could solve it (store not only Y,
#   but Y and Z too + conditions to display the pixel)

# - Broken when coeffX is bigger than coeffY (isometric view from a side,
#   rather them from the bottom).
#   This is probably related to previous problem

# - Ugly resized walls
#   Proper solution is to implement proper affine transormation of the wall
#   image. Challenge is to get original position from transformed image
#   (to fill zBuffer data)

# - Darken parts of some transparent walls
#   This is probably caused by the previous problem

# - All graphics is read from scratch for each map
#   Which is inefficient if you do "ALL" maps

# - I suspect using SubSectors is way more efficient that doing the flood
#   fill (so far I failed figuring out how to make it work)

# TODO LIST:
############
# - multiple PWADs
# - PK3
# - "Zoom" parameter to scale down resulting file
#   (to be able to handle larger maps)

# Imports:
##########

import time
import sys
import os.path
from docopt import docopt
import constants

# Image library to create and manutulate images
from PIL import Image, ImageDraw, ImageFile
# This to make it read internal PNG files (otherwise throws an error)
#ImageFile.LOAD_TRUNCATED_IMAGES = True

# Some basic functions: square, trigonometry fo rotations
import math

# NumPy is way better than Python lists for huge arrays,
# and we have a huge arrays (zBuffer)
import numpy as np

# module to handle PNG format used in modern WADs (PIL's PNG
# capabilities are often not enough for PNGs found in WADs
# install with "pip install pypng"
import png


# if there is \x00 in the name - change all following bytes to \x00
# Some pWADs erroneously have non \x00 symbols in the end,
# this function takes care of it
def trailingZeros(name):
    for i in range(len(name)):
        if name[i] == "\x00":
            updatedName = name[:i+1] + "\x00" * (len(name) - i - 1)
            return updatedName
    return name


# if the name is shorter than 8 bytes, padd it with "\x00"
def addTrailingZeros(name):
    if len(name) < 8:
        return name + "\x00" * (8 - len(name))
    return name


# remove trailing \x00s
def removeTrailingZeros(name):
    for i, char in enumerate(name):
        if char == "\x00":
            return name[:i]
    return name


# Class to hold a lump data
class Lump:

    def __init__(self, data):
        self.data = data
        self.position = 0
        self.length = len(data)

    def reset(self):
        self.position = 0

    def seek(self, newPosition):
        self.position = newPosition

    def read(self, nBytes):
        newPosition = min(self.position + nBytes, len(self.data))
        requestedData = self.data[self.position:newPosition]
        self.position = newPosition
        return requestedData


# Class to read various level information lumps from a WAD
##############################################################
class WADData:

    # open WAD file, read Lump table
    def __init__(self, filename):
        self.fs = open(filename, "rb")
        
        # read main info
        self.wadType, self.numLumps, self.infoTableOfs = self.readWADinfo()
        self.infoTable = self.readWADdirectory()
        self.mapInfoTable = []

        
    # read and return WAD's basic info:
    # WAD type, nukmber of lumps, address of the Directory
    def readWADinfo(self):
        # String: either "IWAD" (main game) or "PWAD" (extra content)
        wadType = self.fs.read(4).decode("utf-8")
        # Total number of lumps (pieces of info)
        numLumps = int.from_bytes(self.fs.read(4), "little", signed=True)
        # Offset where the directory (list of lumps) begins
        infoTableOfs = int.from_bytes(self.fs.read(4), "little", signed=True)
        return wadType, numLumps, infoTableOfs


    # Given the position of the directory and number of lumps,
    # read WAD's directory, return a list of lumps as (position, size, name)
    def readWADdirectory(self):
        self.fs.seek(self.infoTableOfs)
        infoTable = []
        for i in range(self.numLumps):

            # for each entry in the directory, read:
            # Offset (position) of this lump
            filePos = int.from_bytes(self.fs.read(4), "little", signed=True)
            # Size of this lumps, in bytes
            size = int.from_bytes(self.fs.read(4), "little", signed=True)
            # Name of this lump (will be padded by \x00, if sorter than 8 bytes)
            lumpName = self.fs.read(8).decode("utf-8")
            infoTable.append([filePos, size, lumpName])

        return infoTable

    # Given the map name, get all correspondent lumps list
    # that is, vertixes, linedefs, sidedefs, sectors, things
    def setMap(self, mapName):
    
        self.mapInfoTable = []
        mapNameFixed = addTrailingZeros(mapName)
        foundMap = False
        requiredLumps = ["VERTEXES", "LINEDEFS", "SIDEDEFS", "SECTORS\x00", "THINGS\x00\x00"]
        for info in self.infoTable:

            # If the name is the map name we need: start copying
            if mapNameFixed in info[2]:
                foundMap = True
            # Copy the level lumps to a separate list
            if foundMap and info[2] in requiredLumps:
                self.mapInfoTable.append(info)
                del requiredLumps[requiredLumps.index(info[2])]
            # We copied all we needed, done
            if len(requiredLumps) == 0:
                return True

        return False
    
    # Return lump's content (as bytes)
    def getLump(self, lumpName):
        lumpInfo = self.infoTable
        if lumpName in ["VERTEXES", "LINEDEFS", "SIDEDEFS", "SECTORS", "THINGS"]:
            lumpInfo = self.mapInfoTable
        fixedLumpName = addTrailingZeros(lumpName)
        for info in lumpInfo:
            if info[2] == fixedLumpName:
                self.fs.seek(info[0])
                return Lump(self.fs.read(info[1]))
            
    # clean up, close the file
    def __del__(self):
        self.fs.close()



# Classes definitions for main WAD's items:
# vertixes, linedefs, sidedefs, sectors, things
# (and some other classes that are not in WADs, but needed for this program)
############################################

# Verteces: the dots on the XY plane, that everything else connects to
class Vertex:

    def __init__(self, x, y):
        # Just X and Y coordinates
        self.x = x
        self.y = y


# LineDefs: lines connecting vertices that build the geometry of the level.
# Walls or borders of sectors will be connected to LineDefs
class LineDef:

    def __init__(self, beg, end, front, back, topUnpegged, bottomUnpegged):
        # Beginning vertix
        self.beg = beg
        # Ending vertix
        # (Beginning-End order is important, as it defines
        # which side is front and which is back)
        self.end = end
        # N of SideDef (wall) for the front side
        self.front = front
        # N of SideDef for the back side
        self.back = back
        # 1/0. Start drawing bottom and middle textures from the bottom up
        # (usually it is from top down)
        self.topUnpegged = topUnpegged
        # 1/0. Same as previous, but for top textures and the other way round,
        # if this is 1, start drawing from the top (default is from the bottom)
        self.bottomUnpegged = bottomUnpegged


# SideDefs: wall data for each line
class SideDef:

    def __init__(self, xOffset, yOffset, upper, lower, middle, sector):
        # texture offset (to aligh textures on neighbouring walls)
        self.xOffset = xOffset
        self.yOffset = yOffset
        # texture names for ceiling wall (between uneven ceiling parts),
        # middle (regular walls), floor (uneven floor parts)
        self.upper = upper
        self.lower = lower
        self.middle = middle
        # Sector, that this SideDef is facing
        self.sector = sector


# Sectors: areas of the level
class Sector:

    def __init__(self, floorHeight, ceilingHeight,
                 floorTexture, ceilingTexture, light):
        # Height of the floor and ceiling in this area
        self.floorHeight = floorHeight
        self.ceilingHeight = ceilingHeight
        # Textures of the floor and the ceiling in this area
        self.floorTexture = floorTexture
        self.ceilingTexture = ceilingTexture
        # Light level in this area (0, dark - 255, bright)
        self.light = light

        # Following are not part of the WAD,
        # it is to protect from HOM (that would crash the program otherwise))
        # List of all vertixes, surrounding the sector
        self.listOfVerteces = []
        # HOM passed (all vertices are twice in the list) >2 vertixes
        self.HOMpassed = True


# Things: monsters, pickups, other objects on a map
class Thing:

    def __init__(self, x, y, angle, type, options):
        #  location
        self.x = x
        self.y = y
        # which way is it facing
        self.angle = angle
        # type (i.e what mosnter it is)
        # there is a conversion table from numeric thing ID to sprite name,
        # it is inside of the function ParseThing
        self.type = type
        # what difficulty it appear at
        self.options = options
        # Following variables are not part of the WAD
        # Name of the sprite to use
        # (including phase and angle, for example "POSSA1")
        self.sprite = ""
        # Whether to use mirrored sprite
        self.mirrored = False


# This is not part of WAD
# This class contains info needed to draw a wall
# by a wall I mean not only proper walls,
# but floors' and ceilings' side parts too
class Wall:

    def __init__(self, sx, sy, ex, ey, floor, ceiling, texture,
                 xOffset, yOffset, fromTop, position, light, isBack):
        # Start Coordinate
        self.sx = sx
        self.sy = sy
        # End coordinate
        self.ex = ex
        self.ey = ey

        # Height of the bottom and the top of the wall
        # (this is not necessarily ceiling and floor in a room in a game,
        # it could be heights of neighboutring steps of stairs)
        self.floor = floor
        self.ceiling = ceiling

        # texture
        self.texture = texture
        # offset to move texture by
        self.xOffset = xOffset
        self.yOffset = yOffset

        # String. If this is top, bottom or middle part
        # Needed to correctly set the next variable
        self.position = position
        # True/False. If this wall needs to be drawn from (in other words,
        # texture should be aligned with) bottom or top
        self.fromTop = fromTop
        # Light level (0-255). Taken from the adjacent sector
        self.light = light
        # True/False. if this wall was generated from the backside sideDef
        self.isBack = isBack





# Functions to read main lumps (vertixes, linedefs etc)
# and return them as lists of objects
#######################################################

# Read Vertixes from lump data
def getVertixes(lump):
    vertexes = []
    for i in range(lump.length//4):

        x = int.from_bytes(lump.read(2), "little", signed=True)
        # Note: Here I invert Y.
        # This is because in WAD Y axis goes from bottom up,
        # but in PIL it goes from up down
        y = -int.from_bytes(lump.read(2), "little", signed=True)

        # create new Vertex object, return list of Vertex objects
        newVertex = Vertex(x, y)
        vertexes.append(newVertex)

    return vertexes


# Return list of Linedefs from a lump
# zStyle flag switches zDoom new linedef format (similat to Hexen)
def getLineDefs(lump, zStyle=False):
    if lump is None:
        return []
    linedefs = []
    lineDefSize = 16 if zStyle else 14
    for i in range(lump.length // lineDefSize):

        # Beginning and end: vertex'es indexes
        beg = int.from_bytes(lump.read(2), "little", signed=False)
        end = int.from_bytes(lump.read(2), "little", signed=False)

        # Bits that store various properties of a LineDef
        # In our case, we only interested in two flags, see next lines
        flags = int.from_bytes(lump.read(2), "little", signed=False)
        # bit 3: unpegged top (see class description what it means)
        topUnpegged = (flags & 8)//8
        # bit 4: unpegged bottom
        bottomUnpegged = (flags & 16)//16
        lump.read(4)
        if zStyle:
            lump.read(2)

        # front and back sidedefs of this linedef
        front = int.from_bytes(lump.read(2), "little", signed=False)
        back = int.from_bytes(lump.read(2), "little", signed=False)

        # create new LineDef object, return list of LineDef objects
        newLinedef = LineDef(beg, end, front, back,
                             topUnpegged, bottomUnpegged)
        linedefs.append(newLinedef)

    return linedefs


# Return list of SideDefs from a lump
def getSideDefs(lump):
    if lump is None:
        return []
    sidedefs = []
    for i in range(lump.length//30):

        # Offsets to move the texture
        xOffset = int.from_bytes(lump.read(2), "little", signed=True)
        yOffset = int.from_bytes(lump.read(2), "little", signed=True)

        # names of textures for 3 parts of sidedef with some cleanup
        # Cleanup includes:
        # - decoding in ISO-8859-1
        # (UTF-8 can result in an error, albeit rarely)
        # - uppercase (some WADs mix lower and upper case that)
        # - trailing characters after \x00 (it happens not too)
        upper = trailingZeros(lump.read(8).decode("ISO-8859-1").upper())
        lower = trailingZeros(lump.read(8).decode("ISO-8859-1").upper())
        middle = trailingZeros(lump.read(8).decode("ISO-8859-1").upper())

        # sector that this sideDef faces
        sector = int.from_bytes(lump.read(2), "little", signed=False)

        # create new SideDef object, return list of SideDef objects
        newSideDef = SideDef(xOffset, yOffset,
                             upper, lower, middle, sector)
        sidedefs.append(newSideDef)

    return sidedefs


# Return list of Sectors
def getSectors(lump):
    if lump is None:
        return []
    sectors = []
    for i in range(lump.length//26):

        # Heights of the floor and the ceiling
        floorHeight = int.from_bytes(lump.read(2), "little", signed=True)
        ceilingHeight = int.from_bytes(lump.read(2), "little", signed=True)
        # flats' (textures) names for the floor and the ceiling
        floorTexture = trailingZeros(
            lump.read(8).decode("ISO-8859-1").upper())
        ceilingTexture = trailingZeros(
            lump.read(8).decode("ISO-8859-1").upper())
        # lighting level (0-255)
        light = int.from_bytes(lump.read(2), "little", signed=True)
        if light > 255:
            light = 255
        if light < 0:
            light = 0
        lump.read(4)

        # create new Sector object, return list of Sector objects
        newSector = Sector(floorHeight, ceilingHeight,
                           floorTexture, ceilingTexture, light)
        sectors.append(newSector)

    return sectors


# Given list of map's lumps, return list of Things
def getThings(lump, zStyle=False):
    if lump is None:
        return []
    things = []
    thingSize = 20 if zStyle else 10
    for i in range(lump.length//thingSize):

        if zStyle:
            lump.read(2)
            x = int.from_bytes(lump.read(2), "little", signed=True)
            y = -int.from_bytes(lump.read(2), "little", signed=True)
            lump.read(2)
            angle = int.from_bytes(lump.read(2), "little", signed=True)
            type = int.from_bytes(lump.read(2), "little", signed=True)
            options = int.from_bytes(lump.read(2), "little", signed=True)
            lump.read(6)
        else:
            # Coordinates to place the thing at
            x = int.from_bytes(lump.read(2), "little", signed=True)
            # same reason for inverting Y as for vertices
            y = -int.from_bytes(lump.read(2), "little", signed=True)

            # 0-359. Angle at which it is rotated
            # 0 is East, then goes anti-clockwise
            angle = int.from_bytes(lump.read(2), "little", signed=True)

            # Thing's type (what is it)
            # List of types are later in the program
            type = int.from_bytes(lump.read(2), "little", signed=True)

            # bits, which difficulty, match type this thing appears at
            options = int.from_bytes(lump.read(2), "little", signed=True)

        # create new Thing object, return list of Thing objects
        newThing = Thing(x, y, angle, type, options)
        things.append(newThing)

    return things





# Putting it all together: given a WAD filename and a map name
# get all main level geometry data
# (this does not include graphics: flats, textures, sprites)
def getBasicData(wad, zStyle=False):


    pallete = getPallete(wad.getLump("PLAYPAL"))
    colorMap = getColorMap(wad.getLump("COLORMAP"))

    # in map does not exist - leave
    if len(wad.mapInfoTable) == 0:
        # but return infotable and pallete and colormap
        # it is needed for maps with non-standard names
        return False, False, False, False, \
                False, pallete, colorMap

    # otherwise get the geometry + pallete + color map

    vertexes = getVertixes(wad.getLump("VERTEXES"))
    linedefs = getLineDefs(wad.getLump("LINEDEFS"), zStyle)
    sidedefs = getSideDefs(wad.getLump("SIDEDEFS"))
    sectors = getSectors(wad.getLump("SECTORS"))
    things = getThings(wad.getLump("THINGS"), zStyle)

    return vertexes, linedefs, sidedefs,\
        sectors, things, pallete, colorMap


# Functions to facilitate vertixes transformation
# This is for rotation and isometric view fo the map
#######################################################

# Rotate one set of coordinates by rotateDeg degrees
# around the (0,0)
# return new coordinates
def rotatePoint(x, y, rotateDeg):
    rotateRad = math.radians(rotateDeg)
    currAngleRad = math.atan2(y, x)
    dist = math.sqrt(x ** 2 + y ** 2)
    resultAngleRad = currAngleRad + rotateRad
    newy = math.sin(resultAngleRad) * dist
    newx = math.cos(resultAngleRad) * dist
    return int(newx), int(newy)


# Rotate all vertixes and things by "rotate" angle
def applyRotation(vertexes, things, rotate):
    # Just go through all XY coordinates and apply rotatePoint to each
    for vertex in vertexes:
        x, y = vertex.x, vertex.y
        newx, newy = rotatePoint(x, y, rotate)
        vertex.x, vertex.y = newx, newy
    for thing in things:
        x, y = thing.x, thing.y
        newx, newy = rotatePoint(x, y, rotate)
        thing.x, thing.y = newx, newy
        # One extra thing to do for things:
        # if the map rotates, we need to rotate them same degrees
        # in the opposite directions
        # So they would face the same direction relative to the map
        thing.angle -= rotate
        if thing.angle < 0:
            thing.angle += 360


# Scale vertixes and things along Y axis by factor of scaleY
# This is to create isometric view (viewing from a side)
# scaleY is usually 0.5-0.9
def applyScaleY(vertexes, things, scaleY):
    for vertex in vertexes:
        y = vertex.y
        newy = int(y * scaleY)
        vertex.y = newy
    for thing in things:
        y = thing.y
        newy = int(y * scaleY)
        thing.y = newy


# Functions to get various graphic info from lumps (patches, textures, flats)
############################################################################

# Functions that deal with colors
#################################

# Get the pallete (256 colors used in the game)
# Pallete is a list of 256 tuples,
# each tuple has 3 0-255 integers (RGB color)
def getPallete(lump):
    if lump is None:
        return []
    pallete = []
    for i in range(256):
        pixel = []
        for j in range(3):
            pixel.append(int.from_bytes(lump.read(1), "little",
                         signed=False))
        pallete.append(tuple(pixel))
    return pallete


# Get the ColorMap
# Color Map is used to map colors to new colors for various light levels
# Returns list of 34 maps, each map is a list of indexes in pallete to map to
def getColorMap(lump):
    if lump is None:
        return []

    colorMap = []
    for i in range(34):
        colorMap.append([])
        for j in range(256):
            colorMap[-1].append(int.from_bytes(lump.read(1), "little",
                                signed=False))
    return colorMap


# Combines Pallette and ColorMap into Color Conversion table:
# Map which RGB color to which, for various light levels
def genColorConversion(pallete, colorMap):
    colorConv = []
    for i in range(34):
        colorConv.append({})
        for j in range(256):
            colorConv[-1][pallete[j]] = pallete[colorMap[i][j]]
    return colorConv


# Function that deal with pictures in Doom format (including patches)
#####################################################################

# convert PNG data into a PIL pic
# Using external library "pypng" as PIL often can't read WAD's PNG properly
def png2pic(pngdata, pallete):

    # PNG can contain whatever. But we want it to only have
    # pallete colors.
    # Find closest pixel in the pallete
    def closestPix(pixel, pallete):

        # First look in the cached values
        nonlocal palleteMemory
        if pixel in palleteMemory:
            # Need to return the copy, otherwise remaining script
            # updates this value
            return palleteMemory[pixel].copy()

        # Otherwise find the closest pixel (min sum of by-channel differences)
        closest = (0,0,0)
        minDistance = 256 * 4
        for pal in pallete:
            distance = 0
            for i in range(3):
                distance += abs(pixel[i]-pal[i])
                #distance += (pixel[i]-pal[i])**2
            #distance = int(math.sqrt(distance))
            if distance < minDistance:
                minDistance = distance
                closest = pal
        palleteMemory[pixel] = list(closest)
        return list(closest)

    # Dynamic programming to speed up conversion to the pallete colors
    palleteMemory = {}
    
    pngpic = png.Reader(bytes=pngdata)
    width, height, rows, info = pngpic.read(lenient=True)

    # Should resulting byte stream be grouped in 3s or 4s
    bytesize = 3
    if info["alpha"]:
        bytesize = 4

    # resulting image should have alpha channel anyway    
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = im.load()
    
    temppix = []
    # iterating through the resilts of PNG reader
    # and copy pixels to a new PIL Image
    for i, row in enumerate(rows):
        for j, value in enumerate(row):

            # constructing pixel from a bytestream
            temppix.append(value)
            
            # if it is long enough - time to write this byte to the image
            if len(temppix) == bytesize:

                # Check it it is in the pallete
                if tuple(temppix[:3]) not in pallete:
                    # and use closest if it isn't
                    newpix = closestPix(tuple(temppix[:3]), pallete)
                else:
                    newpix = temppix[:3]

                # add transprency byte, or copy from the original
                if bytesize == 3:
                    newpix.append(255)
                else:
                    newpix.append(255 if temppix[3] > 127 else 0)

                # copy the result to the final image, clear the pixel buffer
                px[j//bytesize,i] = tuple(newpix)
                temppix = []

    return im

            
# Get names of patches (texture parts)
# They all are stored in PNAMES lump and will be referenced by ID, not names
def getPatchesNames(lump):
    patchesNames = []
    if lump is None:
        return []
    pNameLen = int.from_bytes(lump.read(4), "little", signed=True)
    for i in range(pNameLen):
        patchesNames.append(trailingZeros(
                            lump.read(8).decode("ISO-8859-1").upper()))
    return patchesNames


# Given lump name of a picture, get that picture, stored in Doom picture format
# Used for patches, sprites, title screens etc (but not flats)
# Picture returned as a PIL.Image object
def getPicture(lump, pallete):
    if lump is None:
        return None

    if lump.data[1:4] == b'PNG':
        pic = png2pic(lump.data, pallete)
        return pic

    # Size of the final picture
    width = int.from_bytes(lump.read(2), "little", signed=False)
    height = int.from_bytes(lump.read(2), "little", signed=False)

    # Protection against some weird humongous things
    # Although textures with 1024 width is a thing
    if width > 2000 or height > 2000:
        return None
    lump.read(4)

    # This is a list of Posts (columns) that comprize an image
    postOffsets = []
    for w in range(width):
        postOffsets.append(int.from_bytes(
            lump.read(4), "little", signed=False))

    # this is the image we will build from posts (columns)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = im.load()

    # Here we go go through all Posts
    for i in range(im.size[0]):
        lump.seek(postOffsets[i])

        # There is no fixed length of a post,
        # post ends with the last byte=255
        while True:
            # if the first byte is not 255 - it is an offset
            topdelta = int.from_bytes(
                lump.read(1), "little", signed=False)
            # if it is 255 - end this post (column)
            if topdelta == 255:
                break
            # Next byte is the length of data to read
            length = int.from_bytes(
                lump.read(1), "little", signed=False)

            # Protection in case something goes wrong
            # and we are at the EOF
            # (removed cause it breaks otehr files)
            # if length == 0:
            #    return im

            # First and last bytes are not used
            lump.read(1)
            # FInally, reading some pixel data
            for j in range(length):
                pixel = int.from_bytes(
                    lump.read(1), "little", signed=False)
                color = pallete[pixel]
                px[i, topdelta + j] = color
            lump.read(1)
    return im


# Get all the pictures in a list
# Returns a dictionary, where key is the picture's name and value is PIL.Image
def getPictures(wad, pictureNames, pallete):
    pictures = {}
    for pictureName in pictureNames:
        pictureLump = wad.getLump(pictureName)
        im = getPicture(pictureLump, pallete)
        if im is not None:
            pictures[pictureName] = im
    return pictures


# Functions that deal with textures
###################################

# Get info about all the textures
# Which is texture data (name, width, height, patches)
# where "patches" is a list of patches and offsets:
# [(offsetX, offsetY, patchN),..]
# They will be put together into a texture in a different function
def getTextureInfo(lump):
    if lump is None:
        return []
    texturesInfo = []
    nTextures = int.from_bytes(lump.read(4), "little", signed=False)
    offsets = []
    for i in range(nTextures):
        offsets.append(int.from_bytes(lump.read(4), "little", signed=False))
    for offset in offsets:
        lump.seek(offset)
        textureName = trailingZeros(lump.read(8).decode("ISO-8859-1"))
        lump.read(4)
        width = int.from_bytes(lump.read(2), "little", signed=False)
        height = int.from_bytes(lump.read(2), "little", signed=False)
        lump.read(4)
        patchCount = int.from_bytes(lump.read(2), "little", signed=False)

        patches = []
        for i in range(patchCount):
            offsetX = int.from_bytes(lump.read(2), "little", signed=True)
            offsetY = int.from_bytes(lump.read(2), "little", signed=True)
            patchN = int.from_bytes(lump.read(2), "little", signed=False)
            lump.read(4)
            patches.append((offsetX, offsetY, patchN))

        texturesInfo.append((textureName, width, height, patches))

    return texturesInfo


# Given a list of texture information (see previous function)
# Create all textures (by constructing them from patches)
# Return dictionary {textureName:PIL.Image}
def getTextures(textureInfo, patches, patchesNames):
    textures = {}
    for texture in textureInfo:

        name, width, height, patchList = texture
        im = Image.new("RGBA", (width, height), color=(0, 0, 0, 0))
        # go through the patches that make up a texture
        for patchData in patchList:
            offx, offy, patchID = patchData
            if patchID >= len(patchesNames):
                continue
            patchName = patchesNames[patchID]
            if patchName in patches:
                # paste it into the image
                # third parameter is a mask,
                # because many patches use transparency
                im.paste(patches[patchName], (offx, offy), patches[patchName])
        textures[name] = im
    return textures


# Function that deal with flats (textures for floors and ceilings)
# We dont use ceilings though, so just floors in this case
###################################################################

# Given flat's name, return the raw flat's content
# list of 1 byte per pixel, each is a code from teh pallete
def getRawFlat(fs, infoTable, flatName):
    for info in infoTable:
        if flatName in info[2]:
            fs.seek(info[0])
            raw = fs.read(info[1])
            return raw


# Convert raw flat data into a 64x64 list of (R,G,B)
# This is not a PIL picture, but just a list of lists of RGB tuples
# [[(R,G,B), (R,G,B), (R,G,B), ...], [], [], ...]
def createFlat(rawFlat, pallete):
    out = []
    pointer = 0
    width = 64
    height = len(rawFlat) // width
    for i in range(width):
        out.append([])
        for j in range(height):
            color = pallete[rawFlat[pointer]]
            out[-1].append(color)
            pointer += 1
    return out


# Given list of sectors, get list of all flats (only floors), used in them
def getListOfFlats(sectors):
    listOfFlats = set()
    for sector in sectors:
        if sector.floorTexture not in listOfFlats:
            listOfFlats.add(sector.floorTexture)
    return list(listOfFlats)


def getListOfTextures(walls):
    listOfTextures = set()
    for wallgroup in walls.values():
        for wall in wallgroup:
            listOfTextures.add(wall.texture)
    return list(listOfTextures)

        
# Given list of flats, return dictionary of flats data (R,G,B) list
# {flatName: [[(R,G,B), (R,G,B), ...], [],[], ...]}
def getFlats(wad, listOfFlats, pallete):
    flats = {}
    for flatName in listOfFlats:
        rawFlat = wad.getLump(flatName)
        if rawFlat is None:
            continue

        # 1. It is a PNG file
        if rawFlat.data[1:4] == b'PNG':
            flatPic = png2pic(rawFlat.data, pallete)
            # it is still in wrong format for a flat
            flat = pic2flat(flatPic)
            flats[flatName] = flat

        # 2. It is a regular DOOM flat
        elif len(rawFlat.data) == 4096:
            flatData = createFlat(rawFlat.data, pallete)
            flats[flatName] = flatData

        # 3. it is a DOOM picture format flat
        elif len(rawFlat.data) != 0:
            flatPic = getPicture(rawFlat, pallete)
            if flatPic is not None:
                flat = pic2flat(flatPic)
                flats[flatName] = flat

    return flats


# couple of helper functions to transform flats (arrays of tuples)
# into PIL's images (pics) and back
def flat2pic(flat):
    width = len(flat)
    height = len(flat[0])
    im = Image.new("RGB", (width, height), color=(0, 0, 0))
    px = im.load()
    for i in range(width):
        for j in range(height):
            px[i, j] = flat[i][j]
    return im


def pic2flat(pic):
    width = pic.size[0]
    height = pic.size[1]
    px = pic.load()
    flat = []
    for i in range(width):
        flat.append([])
        for j in range(height):
            # copying this way to remove 4th element of px tuple
            newpix = (px[i, j][0], px[i, j][1], px[i, j][2])
            flat[-1].append(newpix)
    return flat


# Functions to parse the map data, preparing for the drawing
############################################################

# Check if the sectors are valid
# (HOM stads for Hall Of Mirrors - an effect you see in classic Doom,
# when a sector error is present)
def checkHOM(vertexes, linedefs, sidedefs, sectors):

    # Pouplate listOfVerteces data with list of all vertixes,
    # surrounding this sector
    for linedef in linedefs:
        for sidedef in [linedef.front, linedef.back]:
            if sidedef != 65535 and sidedef < len(sidedefs):
                sector = sidedefs[sidedef].sector
                for vertex in [linedef.beg, linedef.end]:
                    sectors[sector].listOfVerteces.append(vertex)

    # Go through sectors, marking invalid with HOMpassed = False
    # So far we have 2 checks here
    for sector in sectors:
        # Check #1
        # valid sector has at least 3 sides (2 vertexes each)
        # Fixes those dangling forgotten sidedefs (as in DOOM2-MAP30)
        if len(sector.listOfVerteces) < 6:
            sector.HOMpassed = False
            continue
        # Check #2
        # If it is a narrow strip less than 2 pix wide - disqualify
        xs, ys = [], []
        for vertex in sector.listOfVerteces:
            if vertex < len(vertexes):
                xs.append(vertexes[vertex].x)
                ys.append(vertexes[vertex].y)
        if len(xs) == 0 or max(xs) - min(xs) < 2 or max(ys) - min(ys) < 2:
            sector.HOMpassed = False


# Given level's, generate list of Walls objects
# Wall object combines all info needed to draw a wall:
# things like position, texture, type, lighting etc.
# Returned as a dictionary, where key is the proportionate
# to the distance from the corner
# To draw from fartherst to closest, to make semi-transparent back-walls work
def genWalls(vertexes, linedefs, sidedefs, sectors, options):
    hCoefX, hCoefY = options["coefX"], options["coefY"]
    walls = {}

    # All walls are based on Linedefs
    for linedef in linedefs:
        # Get linedef's basic info
        frontSideDef = linedef.front
        backSideDef = linedef.back
        if linedef.beg >= len(vertexes) or linedef.end >= len(vertexes):
            continue
        start = vertexes[linedef.beg]
        end = vertexes[linedef.end]
        distance = (start.x + end.x)/2 * hCoefX + (start.y + end.y)/2 * hCoefY
        isBack = False

        # Middle part (wall proper)
        if frontSideDef < len(sidedefs) and \
           sidedefs[frontSideDef].middle != "-\x00\x00\x00\x00\x00\x00\x00":

            fromTop = True
            sector = sidedefs[frontSideDef].sector
            # Floor and ceiling here - means bottom and top height of the wall
            floor = sectors[sector].floorHeight
            ceiling = sectors[sector].ceilingHeight
            light = sectors[sector].light

            # If it is a double-sided linedef, top and bottom
            # border is calculated a bit differently,
            # you need to take both sides into account
            if backSideDef < len(sidedefs) and backSideDef != 65535:
                backsector = sidedefs[backSideDef].sector
                backfloor = sectors[backsector].floorHeight
                backceiling = sectors[backsector].ceilingHeight
                floor = max(floor, backfloor)
                ceiling = min(ceiling, backceiling)

            # Create a new wall object, put it with the "distance" key
            # Note:
            # If it is a double-sided linedef, we only display front part.
            # Which is not quite right, actually,
            # but it is better than displaying both
            if distance not in walls:
                walls[distance] = []

            newWall = Wall(start.x, start.y, end.x, end.y, floor, ceiling,
                           sidedefs[frontSideDef].middle,
                           sidedefs[frontSideDef].xOffset,
                           sidedefs[frontSideDef].yOffset, fromTop, "middle",
                           light, isBack)
            walls[distance].append(newWall)

        #  Generate bottom and top sidedefs
        if frontSideDef < len(sidedefs) and backSideDef < len(sidedefs) \
           and backSideDef != 65535:

            # get sector and height info from both sides
            frontSector = sidedefs[frontSideDef].sector
            backSector = sidedefs[backSideDef].sector
            frontFloor = sectors[frontSector].floorHeight
            frontCeiling = sectors[frontSector].ceilingHeight
            backFloor = sectors[backSector].floorHeight
            backCeiling = sectors[backSector].ceilingHeight
            # If both sides have ceiling texture F_SKY - if is outdoors,
            # Ignore the top part
            isSky = "F_SKY1" in sectors[backSector].ceilingTexture \
                    and "F_SKY1" in sectors[frontSector].ceilingTexture

            # Bottom part (side of the steps)
            if frontFloor != backFloor:
                fromTop = True
                if linedef.bottomUnpegged:
                    fromTop = False

                top = max(frontFloor, backFloor)
                bottom = min(frontFloor, backFloor)

                if bottom == frontFloor:
                    sideDef = sidedefs[frontSideDef]
                else:
                    sideDef = sidedefs[backSideDef]
                    isBack = True

                if distance not in walls:
                    walls[distance] = []

                light = sectors[sideDef.sector].light
                newWall = Wall(start.x, start.y, end.x, end.y, bottom, top,
                               sideDef.lower, sideDef.xOffset, sideDef.yOffset,
                               fromTop, "bottom", light, isBack)
                walls[distance].append(newWall)

            # Top part (side of the ceiling with different heights)
            if frontCeiling != backCeiling:

                if not isSky:
                    fromTop = False
                    if linedef.topUnpegged == 1:
                        fromTop = True
                    top = max(frontCeiling, backCeiling)
                    bottom = min(frontCeiling, backCeiling)

                    if top == frontCeiling:
                        sideDef = sidedefs[frontSideDef]
                    else:
                        sideDef = sidedefs[backSideDef]
                        isBack = True

                    if distance not in walls:
                        walls[distance] = []

                    light = sectors[sideDef.sector].light
                    newWall = Wall(start.x, start.y, end.x, end.y, bottom, top,
                                   sideDef.upper, sideDef.xOffset,
                                   sideDef.yOffset, fromTop, "top", light,
                                   isBack)
                    walls[distance].append(newWall)

    return walls


# Go through the list of things
# Return two objects:
# 1. dictionaly of things, where key is the distance (similar to walls)
# things in that list are enriched with some additional data, like sprite info
# 2. list of all sprites to be used
# so later we can get them all from the WAD file
def parceThings(things, infoTable, options, stats):

    # Check if sprite with such angle number exists
    # Used to differentiate between object with one or many sprites
    def findSprite(sprite, angle):
        found = ""
        for info in infoTable:
            if sprite in info[2] and angle in info[2]:
                # in case we found sprite
                # or we found second, unmirrored, sprite (A1 instead of A1A3)
                if found == "" or \
                   info[2][6:8] == "\x00\x00" and found[6:8] != "\x00\x00":
                    found = info[2]
        return found

    hCoefX, hCoefY = options["coefX"], options["coefY"]

    # Mapping between ID (as it is used in "things" lumps)
    # and sprite name prefix.
    # this data is not in lumps, it is hardcoded.
    # I took it from the "Unofficial Manual" file
    # Note there was a typo for "TGRN" (was "TGRE" in manual)
    spriteMap = {
        # player
        1: "PLAY",
        # monsters
        3004: "POSS",   84: "SSWV",    9: "SPOS",   65: "CPOS", 3001: "TROO",
        3002: "SARG",   58: "SARG", 3006: "SKUL", 3005: "HEAD",   69: "BOS2",
        3003: "BOSS",   68: "BSPI",   71: "PAIN",   66: "SKEL",   67: "FATT",
          64: "VILE",    7: "SPID",   16: "CYBR",   88: "BBRN",
        # weapons & ammo
        2005: "CSAW", 2001: "SHOT",   82: "SGN2", 2002: "MGUN", 2003: "LAUN",
        2004: "PLAS", 2006: "BFUG", 2007: "CLIP", 2008: "SHEL", 2010: "ROCK",
        2047: "CELL", 2048: "AMMO", 2049: "SBOX", 2046: "BROK",   17: "CELP",
           8: "BPAK",
        # pickups
        2011: "STIM", 2012: "MEDI", 2014: "BON1", 2015: "BON2", 2018: "ARM1",
        2019: "ARM2",   83: "MEGA", 2013: "SOUL", 2022: "PINV", 2023: "PSTR",
        2024: "PINS", 2025: "SUIT", 2026: "PMAP", 2045: "PVIS",    5: "BKEY",
          40: "BSKU",   13: "RKEY",   38: "RSKU",    6: "YKEY",   39: "YSKU",
        # Objects and decoration
        2035: "BAR1",   72: "KEEN",   48: "ELEC",   30: "COL1",   32: "COL3",
          31: "COL2",   36: "COL5",   33: "COL4",   37: "COL6",   47: "SMIT",
          43: "TRE1",   54: "TRE2", 2028: "COLU",   85: "TLMP",   86: "TLP2",
          34: "CAND",   35: "CBRA",   44: "TBLU",   45: "TGRN",   46: "TRED",
          55: "SMBT",   56: "SMGT",   57: "SMRT",   70: "FCAN",   41: "CEYE",
          42: "FSKU",   49: "GOR1",   63: "GOR1",   50: "GOR2",   59: "GOR2",
          52: "GOR4",   60: "GOR4",   51: "GOR3",   61: "GOR3",   53: "GOR5",
          62: "GOR5",   73: "HDB1",   74: "HDB2",   75: "HDB3",   76: "HDB4",
          77: "HDB5",   78: "HDB6",   25: "POL1",   26: "POL6",   27: "POL4",
          28: "POL2",   29: "POL3",   24: "POL5",   79: "POB1",   80: "POB2",
          81: "BRS1",
        # Dead things (5 letters, sprite + animation from the last letter)
          15: "PLAYN",   18: "POSSL",   19: "SPOSL",   20: "TROOM",
          21: "SARGN",   22: "HEADL",   10: "PLAYW",   12: "PLAYW",
        }

    # And this are the names to look out for and count
    # for usage in map statistics
    statsNames = {"POSS": "21Zombieman", "SPOS": "22Shotgunner",
        "TROO": "23Imp", "SSWV": "24Wolfenstein SS", "CPOS": "25Chaingunner",
        "SARG": "26Pinky", "SKUL": "28Lost Soul", "HEAD": "29Cacodemon",
        "BOS2": "30Hell Knight", "BOSS": "31Baron of Hell",
        "BSPI": "32Arachnotron", "PAIN": "33Pain Elemental",
        "SKEL": "34Revenant", "FATT": "35Mancubus", "VILE": "36Arch-vile",
        "SPID": "37Spider Mastermind", "CYBR": "38Cyberdemon",
        "BBRN": "39John Romero"}

    thingsList = {}
    sprites = set()

    # For things that have several frames of sprites (monsters), use
    # one of these frames
    spriteFrames = "ABCD"
    # It will round-robin through those frames, this is the counter for it
    spriteFrameCount = 0

    for thing in things:

        # ignore DM things
        # only show things for difficulty level = UV
        if thing.options & 16 == 16 or thing.options & 4 != 4:
            continue

        if thing.type in spriteMap:

            # Get the sprite prefix
            thingName = spriteMap[thing.type]
                
            # If it is in the statsNames: count it to the statistics
            if thingName in statsNames:
                commonName = statsNames[thingName]
                # Correction for Spectres: they have the same sprite as
                # Pinkies, but different thing type ID
                if thing.type == 58:
                    commonName = "27Spectre"
                if commonName in stats:
                    stats[commonName] += 1
                else:
                    stats[commonName] = 1

            # given the 0-360 angle in thing's data,
            # calculate sprite number to use
            angle = (14 - thing.angle//45) % 8 + 1

            # First, try to find a sprite with frame 0
            # (most non-mosnter objects)
            if len(thingName) == 4:
                sprite = findSprite(thingName, "A0")
            # these are dead things, already have frame name in the thing name
            elif len(thingName) == 5:
                sprite = findSprite(thingName,  "0")

            # If nothing is found, then
            # find a sprite with 1-8 frame
            if sprite == "":
                if len(thingName) == 4:
                    sprite = findSprite(thingName,
                        spriteFrames[spriteFrameCount % 4] + str(angle))
                    spriteFrameCount += 1
                elif len(thingName) == 5:
                    sprite = findSprite(thingName, str(angle))

            # if it is one of those A1A3 sprites (and we use the second one)
            # than it is a mirrored one, so set the flag
            if len(sprite) == 8 and str(angle) == sprite[7]:
                thing.mirrored = True

            # Add sprite name to the thing object
            thing.sprite = sprite

            # Add that thing object to a dictionary with distance as key
            distance = thing.x * hCoefX + thing.y * hCoefY
            if distance not in thingsList:
                thingsList[distance] = []
            thingsList[distance].append(thing)

            # also, add sprite name to the list of sprites
            # (currently set to ignore duplictes), make it list on return
            sprites.add(thing.sprite)

    return thingsList, list(sprites)


# Some other graphics helper functions, for drawing
############################################

# Calculate out file's size and offset to use for WAD's coordinates
def getImageSizeOffset(vertexes, linedefs, sidedefs, sectors, options):
    margins, hCoefX, hCoefY = \
        options["margins"], options["coefX"], options["coefY"]
    minX, minY, maxX, maxY = 100000, 100000, -100000, -100000
    # Basically we go through all linedefs, their vertexes,
    # and the the floor and the ceiling of walls attached to them
    # calculating the minimum and maximum
    for linedef in linedefs:
        for sidedef in [linedef.front, linedef.back]:
            if sidedef == 65535 or sidedef >= len(sidedefs):
                continue
            sectorN = sidedefs[sidedef].sector
            sector = sectors[sectorN]
            for height in [sector.floorHeight, sector.ceilingHeight]:
                for vertex in [linedef.beg, linedef.end]:
                    if vertex >= len(vertexes):
                        continue
                    minX = min(minX, vertexes[vertex].x)
                    maxX = max(maxX, vertexes[vertex].x)
                    minY = min(minY, vertexes[vertex].y)
                    maxY = max(maxY, vertexes[vertex].y)
                    x = int(vertexes[vertex].x - height * hCoefX)
                    y = int(vertexes[vertex].y - height * hCoefY)
                    minX = min(minX, x)
                    maxX = max(maxX, x)
                    minY = min(minY, y)
                    maxY = max(maxY, y)

    # Add margin twice: there's image size
    # Margin minus minimum is an offset to convert XY coordinate
    # to image coordinates
    return maxX - minX + 2 * margins, maxY - minY + 2 * margins,\
                -minX + margins, -minY + margins


# Given a linedef, find coordinates of a point to start floodfill from
# it is 1 pixel sideways from linedef's center
# "right" determines if it sideways means right or left
# "right" side means if you are looking from Beginning to End of linedef
def findFloodPoint(linedef, vertexes, right=True):

    # read coordinates from vertexes data, calculate the center
    beg = linedef.beg
    end = linedef.end
    if beg >= len(vertexes) or end >= len(vertexes):
        return -1000000, -1000000

    x1 = vertexes[beg].x
    y1 = vertexes[beg].y
    x2 = vertexes[end].x
    y2 = vertexes[end].y
    x = (x1+x2)//2
    y = (y1+y2)//2

    # too short a linedef, let's ignore this one to be safe
    if abs(x2 - x1) <= 2 and abs(y2 - y1) <= 2:
        return -1000000, -1000000

    # sideways distance. d=1 seems to work best
    d = 1
    # find right side
    if right:
        if x2 > x1:
            y += d
        if x2 < x1:
            y -= d
        if y2 > y1:
            x -= d
        if y2 < y1:
            x += d
    # or the left side
    else:
        if x2 > x1:
            y -= d
        if x2 < x1:
            y += d
        if y2 > y1:
            x += d
        if y2 < y1:
            x -= d
    return x, y


# This is a weird one. But I need it.
# Basically, you give it two XY coordintes
# and it returns a list of XY coordinates of a line
# connecting those two points
# Used as a part of drawing walls
def getLinePixels(beg, end):
    if beg == end:
        return [beg]
    x1, y1 = beg
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    pixels = []

    if abs(dx) > abs(dy):
        s = dx // abs(dx)
        for x in range(x1, x2 + s, s):
            y = int(y1 + dy * ((x - x1) / dx))
            if x != x1:
                # One little but important detail
                # the line cannot go horizontally and verticaly
                # at the same time. If it does, add a pixel in between
                # Without this thing walls have holes in them
                if x != pixels[-1][0] and y != pixels[-1][1]:
                    pixels.append((pixels[-1][0], y))
            pixels.append((x, y))
    else:
        s = dy // abs(dy)
        for y in range(y1, y2 + s, s):
            x = int(x1 + dx*((y - y1) / dy))
            if y != y1:
                if x != pixels[-1][0] and y != pixels[-1][1]:
                    pixels.append((x, pixels[-1][1]))
            pixels.append((x, y))
    return pixels


# Make a lighting conversion for im image
# Return image with lightng applied
# Done through applying mapping from colorConversion
def lightImage(im, light, colorConversion):
    px = im.load()
    for i in range(im.size[0]):
        for j in range(im.size[1]):
            opacity = px[i, j][3]
            rawColor = (px[i, j][0], px[i, j][1], px[i, j][2])
            litColor = list(colorConversion[light][rawColor]) + [opacity]
            px[i, j] = tuple(litColor)
    return im


# Apply gamma corretion to an image
# (in place, so does not return anything)
# gamma < 1 will lighten the image
#   by default 0.7 gamma applied to the final image
#   (as it usually a bit dark)
# gamma > 1 will darken the image
#   used for Spectres
def gammaCorrection(im, gamma):
    px = im.load()
    for i in range(im.size[0]):
        for j in range(im.size[1]):
            if px[i, j][:3] == (0, 0, 0):
                continue
            pixel = []
            for k in range(3):
                data = px[i, j][k]
                pixel.append(int((data / 255) ** gamma * 255))
            px[i, j] = tuple(pixel)


# Functions that are used in actual drawing of the final picture
################################################################

# Given the Wall object, return wall image
# That is, texture applied to a rectangle of wall's size
# Lighting, offsets and "unpegged-ness" are applied here too
def getWallImage(wall, textures, colorConversion, scaleY):
    # Just unpacking data for convenience
    ceiling, floor, sx, sy, ex, ey, texture,\
        xOff, yOff, fromTop, position, light = \
        wall.ceiling, wall.floor, wall.sx, wall.sy, wall.ex, wall.ey, \
        wall.texture.upper(), wall.xOffset, wall.yOffset, wall.fromTop, \
        wall.position, wall.light

    # This means no texture
    if texture == "-\x00\x00\x00\x00\x00\x00\x00":
        return False

    # This means either there is a missing texture
    # or I screwd up somewhere
    if texture not in textures:
        return False

    # Wall's "physical" size
    height = ceiling - floor
    # "/ scaleY" to compensate for distortion of isometric projection,
    # if we squeeze Y axis, wall "physical size should remain the same
    width = int(math.sqrt((sx - ex) ** 2 + ((sy - ey) / scaleY) ** 2))

    # Negative width is impossible, but negative height
    # is an error that I saw a few times
    if height <= 0 or width <= 0:
        return False

    textim = textures[texture]
    im = Image.new("RGBA", (width, height), color=(0, 0, 0, 0))

    # Correction of excessive xOffset
    while xOff > textim.size[0]:
        xOff -= textim.size[0]
    while xOff < -textim.size[0]:
        xOff += textim.size[0]

    # Here we paste texture to the canvas
    # TODO: Calculate i and j more elegantly
    # I did budget 1 extra texture width to the left and 3 to the right
    # but it will not be enough with some wild offest values
    for i in range(-1, im.size[0] // textim.size[0] + 3):
        for j in range(-1, im.size[1] // textim.size[1] + 3):

            # Two different ways of pasting textures:
            # FromTop (align top of the wall /top of the texture)
            # Used for regular middles, regular bottom and unpegged tops
            if fromTop:
                im.paste(textim, (i * textim.size[0] - xOff,
                                  j * textim.size[1] - yOff), textim)
            else:
                if position == "top":
                    # regular tops
                    im.paste(textim, (i * textim.size[0] - xOff,
                             im.size[1] - j * textim.size[1] - yOff), textim)
                else:
                    # upegged bottoms
                    im.paste(textim, (i*textim.size[0]-xOff,
                            im.size[1] - j * textim.size[1] -
                            yOff - (floor % 128)), textim)
    lightLevel = 31 - light // 8
    im = lightImage(im, lightLevel, colorConversion)
    return im


# Draw a wall on a final picture
# Among other things this function is given "coords":
# "coords" is 4-point polygon that this wall should fill
# (all calculations already been done at this point)
def pasteWall(bgpx, coords, wall, textures, zBuffer, offsetX, offsetY,
                 colorConversion, options):
    hCoefX, hCoefY, scaleY = \
        options["coefX"], options["coefY"], options["scaleY"]

    # get the wall image
    fgim = getWallImage(wall, textures, colorConversion, scaleY)
    if not fgim:
        return

    # unpack polygone coordinates
    x1, y1, x2, y2, x3, y3, x4, y4 = coords

    # Now the weird stuff:
    # The way I draw that polygon is I draw two lines:
    # along the floor (bottom) and along the ceiling (top) of the wall
    # and then series of lines between each point of floor and ceiling lines
    # The reason it is done this way is that it allows me to track physical
    # location of each pixel (which is needed for zBuffer
    # and correct overlapping of objects
    floorLine = getLinePixels((x1, y1), (x2, y2))
    ceilingLine = getLinePixels((x4, y4), (x3, y3))
    newW = max(abs(x2 - x1) + 1, len(floorLine))
    newH = abs(y4 - y1) + 1
    for i in range(min(len(floorLine), len(ceilingLine))):
        newH = max(newH, len(getLinePixels(ceilingLine[i], floorLine[i])))

    # Wall Image is resized to the number of pixel in those lines
    imres = fgim.resize((newW, newH), Image.LANCZOS)

    # Checks if the wall faces away (from isomeric view)
    # set isTransparent if it is
    isTransparent = False
    if x2 <= x1 and y2 >= y1 \
       or hCoefY != 0 and x2 < x1 and (x1 - x2) / (y1 - y2) > hCoefX / hCoefY \
       or hCoefY != 0 and y2 > y1 and (x2 - x1) / (y2 - y1) < hCoefX / hCoefY:
        isTransparent = True
    # for walls made from back SideDefs, it is the other way round
    if wall.isBack:
        isTransparent = not isTransparent

    # Here the actual copying of pixel begins
    px = imres.load()
    for i in range(min(len(floorLine), len(ceilingLine))):
        line = getLinePixels(ceilingLine[i], floorLine[i])
        for j in range(len(line)):

            # Check if we are within the image
            # Now obsolete, as now we have margins calculated
            # including all possibel heights and lows
            if line[j][0] < 0 or line[j][1] < 0\
                    or line[j][0] >= zBuffer.shape[0]\
                    or line[j][1] >= zBuffer.shape[1]:
                continue

            # get the value from the zBuffer
            # actually only y matters in this implementation
            lastZ = zBuffer[line[j][0], line[j][1]]
            height = int((j / newH) * fgim.size[1] + wall.floor)
            x = int((i / len(floorLine)) * (wall.ex - wall.sx) +
                     wall.sx + offsetX)
            y = int((i / len(floorLine)) * (wall.ey - wall.sy) +
                     wall.sy + offsetY)

            # if y is bigger (closer to viewer): draw the pixel
            if lastZ is None or y > lastZ:

                # use the trasnparency from an image
                opacity = px[i, j][3]
                # or 80 for facing away walls
                if isTransparent:
                    opacity = 80

                mixedpix = []
                for k in range(3):
                    mixedpix.append((bgpx[line[j]][k] * (255 - opacity) +
                                      px[i, j][k] * opacity) // 255)
                bgpx[line[j]] = tuple(mixedpix)

                # Keep tracking latest value in zBuffer
                if opacity >= 80:
                    zBuffer[line[j][0], line[j][1]] = y

    # I guess this is redundunt? I did it in attempt to save memory
    fgim.close()
    imres.close()


# Make a "transparent" sprite
# Used for Spectres
# Reads current image where sprite's pixels are, distorts them and returns
def makeTransparentSprite(sprite, px2, x, y, colorConversion):

    # fuzz table, used to distort the background
    # (taken from actual Doom source code)
    fuzz = [1, -1, 1, -1, 1, 1, -1, 1, 1, -1, 1, 1, 1, -1, 1, 1, 1, -1, -1, -1,
            -1, 1, -1, -1, 1, 1, 1, 1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, -1,
            -1, -1, 1, 1, 1, 1, -1, 1, 1, -1, 1]

    # canvas to build the sprite, the size of the pinkie
    spectre = Image.new("RGBA", (sprite.size[0], sprite.size[1]), (0, 0, 0, 0))
    sp = spectre.load()

    # pinkie sprite
    mask = sprite.load()

    # counter to iterate over the fuzz table
    fuzzN = 0

    # go overthe canvas
    for i in range(spectre.size[0]):
        for j in range(spectre.size[1]):

            # if this pixel exists on the mask
            if mask[i, j][3] == 255:
                picX = x - sprite.size[0] // 2 + i
                picY = y - sprite.size[1] + j + fuzz[fuzzN]
                # copy it from the background
                sp[i, j] = px2[picX, picY]
                fuzzN += 1
                if fuzzN == len(fuzz):
                    fuzzN = 0

    # original logic was to apply ColorMap N6, but it didn't look too visible
    # in darker places (duh...), so I just apply gamma conversion the image
    gammaCorrection(spectre, 1.3)
    return spectre


# Draw a thing on the final image
def pasteThing(px2, x, y, atHeight, light, thing, sprites, zBuffer,
               offsetX, offsetY, colorConversion):
    if thing.sprite not in sprites:
        return
    sprite = sprites[thing.sprite].copy()

    # Mirror if needed
    if thing.mirrored:
        sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)

    # This is a Spectre:
    # make a special sprite from distorted background
    if thing.type == 58:
        sprite = makeTransparentSprite(sprite, px2, x, y, colorConversion)
    else:
        # not Spectre
        lightLevel = 31 - light//8
        sprite = lightImage(sprite, lightLevel, colorConversion)

    spx = sprite.load()

    # Draw pixels
    # Go throught the sprite image
    for i in range(sprite.size[0]):
        for j in range(sprite.size[1]):
            # if it is not a transparent pixel
            if spx[i, j][3] != 0:

                # calculate position on the image
                picX = x - sprite.size[0] // 2 + i
                picY = y - sprite.size[1] + j

                # Check if the sprite is still within the picture
                if picX < 0 or picX >= zBuffer.shape[0] or \
                    picY < 0 or picY >= zBuffer.shape[1]:
                    continue

                # get zBuffer data
                lastZ = zBuffer[picX, picY]

                # calculate physical coordinates (we only use physY, actually)
                height = atHeight + j
                physX = thing.x + offsetX
                physY = thing.y + offsetY
                # if it closer than the one in zBuffer - draw
                if lastZ is None or physY > lastZ:
                    px2[picX, picY] = spx[i, j]
                    zBuffer[picX, picY] = physY

    sprite.close()


# Do the actual drawing
def drawMap(vertexes, linedefs, sidedefs, sectors, flats, walls,
            textures, thingsList, sprites, colorConversion, options):

    # do the floodfill in the blueprint image, starting from startPix pixel
    # also with each drawn pixel add data to sectorData array
    # (to know which coordinate is part of which sector)
    # returns False if there is a problem (sector overspils over the boundary)
    def floodFill(sector, startPix):
        nonlocal im
        nonlocal draw
        nonlocal px
        nonlocal sectorData

        toGo = []
        # if starting point is cyan (already filled) or white (border),
        # don't do anything (it will bypass while and exit)
        if px[startPix] != (0, 255, 255) and px[startPix] != (255, 255, 255):
            toGo.append(startPix)

        # Naive Flood Fill algorithm
        # Add eligebale neighbouors to the ToGo list,
        # keep doing while list is not empty
        while len(toGo) > 0:
            thisPix = toGo.pop()
            px[thisPix] = (0, 255, 255)
            sectorData[thisPix[0], thisPix[1]] = sector
            for dx, dy in [(-1, 0), (0, -1), (1, 0), (0, 1)]:
                nextPix = (thisPix[0] + dx, thisPix[1] + dy)
                # If we reached border, something if wrong, return False
                if nextPix[0] < 0 or nextPix[0] == im.size[0] \
                        or nextPix[1] < 0 or nextPix[1] == im.size[1]:
                    return False
                if px[nextPix] != (0, 255, 255) \
                        and px[nextPix] != (255, 255, 255) \
                        and nextPix[0] >= 0 and nextPix[1] >= 0 \
                        and nextPix[0] < im.size[0] \
                        and nextPix[1] < im.size[1]:
                    toGo.append(nextPix)
        return True

    # Expand SecorData by 1 pix (to eliminate seams between sectors)
    def fillSeams(sectorData):
        nonlocal im
        nonlocal px
        # Go thorugh pixels on the blueprint, if it is white (border),
        # Look at surrounding pixels.
        # Replace this pixel with the first valid neighbour sector.
        for i in range(im.size[0]):
            for j in range(im.size[1]):
                if px[i, j] == (255, 255, 255):
                    maxNeighbour = -1
                    for di, dj in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                        if sectorData[i + di, j + dj] is not None and \
                                px[i + di, j + dj] != (0, 0, 255):
                            maxNeighbour = max(maxNeighbour,
                                           sectorData[i + di][j + dj])
                    if maxNeighbour > -1:
                        sectorData[i, j] = maxNeighbour
                        px[i, j] = (0, 0, 255)

    # unpack options
    hCoefX, hCoefY, rotate, scaleY = \
            options["coefX"], options["coefY"], \
            options["rotate"], options["scaleY"]

    # Determine image size and offset between XY in data and XY in image
    imSizeX, imSizeY, offsetX, offsetY = \
        getImageSizeOffset(vertexes, linedefs, sidedefs, sectors, options)

    if options["verbose"]:
        print ("Image size:", imSizeX, imSizeY)
        print ("Blueprint:")

    # Blueprint image: this image is to draw linedefs and flood-fill
    # them with sectors
    im = Image.new("RGB", (imSizeX, imSizeY), (0, 0, 0))
    draw = ImageDraw.Draw(im)

    # Draw Vertixes (not used in final drawing, but this can be used
    # if you are curious what vertixes look like)
    # Radius of circle that represent vertixes
    '''
    s = 10
    for vertex in vertexes:
        x,y = vertex.x, vertex.y
        draw.ellipse((x - s + offsetX, y - s + offsetY,
               x + s + offsetX, y + s + offsetY), fill=(255, 0, 0))
    '''

    # Draw Linedefs on the blueprint
    for linedef in linedefs:

        if linedef.beg >= len(vertexes) or linedef.end >= len(vertexes):
            continue
        x1 = vertexes[linedef.beg].x + offsetX
        y1 = vertexes[linedef.beg].y + offsetY
        x2 = vertexes[linedef.end].x + offsetX
        y2 = vertexes[linedef.end].y + offsetY
        draw.line((x1, y1, x2, y2), fill=(255, 255, 255), width=1)

    # This NP array is the size of the image and is used to store sector data.
    # Each value will be either -1 (undeterment) or a sector number
    sectorData = np.full((imSizeX, imSizeY), -1, dtype=np.int16)

    # Flood fill sectors on the blueprint image (and populate sectorData)
    px = im.load()
    # we go through linedefs and whereever there is sidedef,
    # start flood filling in front of it
    notches = [int(len(linedefs)/100*i) for i in range(100)]
    for n, linedef in enumerate(linedefs):
        if n in notches and options["verbose"]:
            print ("*", end="")
        # we'll need side == 0/1 to determine whether to flood fill
        # from the right or left side of the linedef
        for side, sidedef in enumerate([linedef.front, linedef.back]):
            if sidedef != 65535 and sidedef < len(sidedefs):

                sector = sidedefs[sidedef].sector
                if sectors[sector].HOMpassed:

                    right = True if side == 0 else False
                    x, y = findFloodPoint(linedef, vertexes, right)
                    # x==-1000000 means a problem finding the flood point
                    # (probaly linedefs are too crowded)
                    if x == -1000000:
                        continue
                    # flood Fill returns False if there is an error, e.g.
                    # it overspills and reaches the border of the image.
                    # Ignore such sector
                    if not floodFill(sector, (x + offsetX, y + offsetY)):
                        sectors[sector].HOMpassed = False

    # Not we have a blueprint, with while linedefs and filled sectors
    # We dont need linedefs in the bluprint anymore, besides, they will
    # leave "seams" on the final image. To fix it, fill seams' in sectorData
    # array with neighbouring sectors' data
    fillSeams(sectorData)

    if options["verbose"]:
        print (" Done")
        print ("Drawing sectors: ")

    # another NP array: zBuffer
    # well, it is actually a yBuffer in this case, but let's keep the name
    # It stores phisical coords of the pixel at this place.
    # (to ensure correct overlap if several objects)
    # -32768 means it is empty, otherwise it contains Y coordinate
    # dtype=np.int16 to save memory (this thing can be huge)
    zBuffer = np.full((imSizeX, imSizeY), -32768, dtype=np.int16)

    # Here's the "Image" object for the final picture
    # (with pixel access and drawing access)
    im2 = Image.new("RGB", (imSizeX, imSizeY), (0, 0, 0))
    px2 = im2.load()
    draw2 = ImageDraw.Draw(im2)

    # Go through all pixels, and if there is secorData for this sector,
    # Draw sectors
    notches = [int(imSizeX/100*i) for i in range(100)]
    for i in range(imSizeX):
        if i in notches and options["verbose"]:
            print ("*", end="")

        for j in range(imSizeY):
            if sectorData[i, j] != -1:

                # prepare info about this sector:
                sector = sectorData[i, j]
                if sectors[sector].HOMpassed is False:
                    continue
                floorHeight = sectors[sector].floorHeight
                light = 31 - sectors[sector].light // 8
                flat = sectors[sector].floorTexture
                # pixel will be moved on the picture according to the
                # floor heihgt and hCoefX / hCoefY
                hx, hy = int(floorHeight * hCoefX), int(floorHeight * hCoefY)

                # this is an obsolete check that resulting pixel coordinate is
                # within the image.
                if i - hx < 0 or j-hy < 0 or i - hx >= zBuffer.shape[0] \
                        or j - hy >= zBuffer.shape[1]:
                    continue

                # check zBuffer if we should display this pixel
                lastZ = zBuffer[i - hx, j - hy]
                if lastZ is None or j > lastZ:

                    # check if this flat is missing
                    if flat not in flats:
                        continue

                    # calculate coordinate back in the game's coordinates,
                    # transform it back from isometrics
                    originalX = i - offsetX
                    originalY = j - offsetY - 1
                    if scaleY != 1:
                        originalY = originalY // scaleY
                    if rotate != 0:
                        originalX, originalY = \
                            rotatePoint(originalX, originalY, -rotate)

                    # use those transformed back coordinates to get flat's
                    # pixel reversed X and Y (because of teh way we read it)
                    flatH = len(flats[flat])
                    flatW = len(flats[flat][0])
                    rawColor = flats[flat][originalY % flatH][originalX % flatW]

                    # apply lighting level
                    litColor = colorConversion[light][rawColor]

                    # draw, update the zBuffer
                    px2[i - hx, j - hy] = litColor
                    zBuffer[i - hx, j - hy] = j

    if options["verbose"]:
        print (" Done")
        print ("Drawing walls and things: ")

    # Draw Walls & Things

    # Combine keys (distance) from Walls and Thing lists, iterate through them
    # The idea is to draw Walls and Things from the farthers to closest
    combinesList = sorted(list(set(list(walls.keys()) +
                            list(thingsList.keys()))))
    notches = [int(len(combinesList)/100*i) for i in range(100)]
    for n, distance in enumerate(combinesList):
        if n in notches and options["verbose"]:
            print ("*", end="")

        # Iterate through walls at this key
        if distance in walls:
            for wall in walls[distance]:

                # Calculate coordinates of a polygone, this wall shoudl occupy
                wallHeight = wall.ceiling - wall.floor
                hx, hy = int(wall.floor * hCoefX), int(wall.floor * hCoefY)
                x1, y1 = wall.sx + offsetX, wall.sy + offsetY
                x2, y2 = wall.ex + offsetX, wall.ey + offsetY
                coords = (x1 - hx, y1 - hy, x2 - hx, y2 - hy,
                          int(x2 - hx - wallHeight * hCoefX),
                          int(y2 - hy - wallHeight * hCoefY),
                          int(x1 - hx - wallHeight * hCoefX),
                          int(y1 - hy - wallHeight * hCoefY))
                # Draw the wall
                pasteWall(px2, coords, wall, textures, zBuffer,
                             offsetX, offsetY, colorConversion, options)

        # Iterate through Thins at this key
        if distance in thingsList:
            for thing in thingsList[distance]:

                # thing's coordinates, on the image
                picX = thing.x + offsetX
                picY = thing.y + offsetY

                # Check if the thing is within the picture
                if picX < 0 or picX >= sectorData.shape[0] or \
                    picY < 0 or picY >= sectorData.shape[1]:
                    continue

                # Sector this this sits on
                sector = sectorData[picX, picY]

                # Sometimes thing is right at the crossing of 4 sectors
                # In this case filling algorithm leaves this pixel empty
                # So if there is no secotr, just try 1 pixel up
                if sector is None:
                    sector = sectorData[picX, picY - 1]
                # if there is still no sector - ignore this thing
                if sector is None:
                    continue

                # Calculate coordinates to display this thing at
                atHeight = sectors[sector].floorHeight
                light = sectors[sector].light
                hx, hy = int(atHeight * hCoefX), int(atHeight * hCoefY)
                x, y = picX - hx, picY - hy
                # Draw the thing
                pasteThing(px2, x, y, atHeight, light, thing, sprites,
                            zBuffer, offsetX, offsetY, colorConversion)

    if options["verbose"]:
        print (" Done")

    return im2


# Display map statistics information in the left bottom corner
# title pic is the pWAD's title image
# all stat data in stats dictionary
def drawStats(im, titlepic, stats):

    titleheight = 200
    stats["00Statistics:"] = ""
    stats["20Monsters:"] = ""
    stats[
        "99This image is generated with WAD2PIC python script by " +
        "GamesComputersPlay. Source code at " +
        "https://github.com/gamescomputersplay/wad2pic"] = ""
    color = (255, 255, 255)

    # cur stores the current positin to draw / write at
    cur = [50, im.size[1] - 50 - titleheight]

    # If we have a title pic - display it
    if titlepic is not None:
        im.paste(titlepic, tuple(cur))
        cur[0] += titlepic.size[0] + 50
    cur[1] += 20

    draw = ImageDraw.Draw(im)

    # iterate through statistics dict, print info from it on the picture
    for key in sorted(stats.keys()):

        # First two charater are number to sort keys by
        k = key[2:]
        v = stats[key]

        # "This imageis generate" goes to the bottom
        if key[:2] == "99":
            draw.text((50, im.size[1] - 25), k, color)
            continue

        # Start new column if:
        # - we got to close to the bottom edge
        # - it says "Monsters"
        if im.size[1] - cur[1] < 70 or "Monsters" in k:
            cur[0] += 250
            cur[1] = im.size[1] - 30 - titleheight

        # Draw the text
        draw.text(cur, k.upper(), color)
        draw.text((cur[0]+110, cur[1]), str(v), color)

        # move to the next line
        cur[1] += 20


# Base Function: given iWAD, pWAD and Map name, prepare all the data,
# call the drawing function, save the resulting image
def generateMapPic(iWAD, options, mapName, pWAD=None):

    stats = {}

    # get iWAD data
    iData = WADData(iWAD)
    iData.setMap(mapName)
    vertexes, linedefs, sidedefs, sectors, \
        things, pallete, colorMap = getBasicData(iData)

    # get pWAD data

    if pWAD is not None:
        pData = WADData(pWAD)
        pData.setMap(mapName)
        zStyle = options["zStyle"]
        vertexesP, linedefsP, sidedefsP, sectorsP, \
            thingsP, palleteP, colorMapP = getBasicData(pData, zStyle)


        # Combine iWAD and pWAD data
        if len(vertexesP) > 0:
            vertexes = vertexesP
        if len(linedefsP) > 0:
            linedefs = linedefsP
        if len(sidedefsP) > 0:
            sidedefs = sidedefsP
        if len(sectorsP) > 0:
            sectors = sectorsP
        if len(thingsP) > 0:
            things = thingsP
        if len(palleteP) > 0:
            pallete = palleteP
        if len(colorMapP) > 0:
            colorMap = colorMapP

    if options["verbose"]:
        print ("=" * 40)
        print ("Getting geometry: Done")
        print (f"Stat: {len(vertexes)} vrt, {len(linedefs)} lnd, "+
               f"{len(sidedefs)} sdf, {len(sectors)} sct, {len(things)} thn")
    # Rotate vertixes and things
    rotate = options["rotate"]
    if rotate != 0:
        applyRotation(vertexes, things, rotate)

    # Scale vertixes along Y
    scaleY = options["scaleY"]
    if scaleY != 1:
        applyScaleY(vertexes, things, scaleY)

    # Check if sectors are valid (invalid may crash the program)
    checkHOM(vertexes, linedefs, sidedefs, sectors)

    # Generate walls
    # (more detailed info, neede to draw walls)
    walls = genWalls(vertexes, linedefs, sidedefs, sectors, options)
    
    # get Flats (textures of floors)
    requiredFlats = getListOfFlats(sectors)
    flats = getFlats(iData, requiredFlats, pallete)
    # Update flats from pWAD
    if pWAD is not None:
        flatsP = getFlats(pData, requiredFlats, pallete)
        flats.update(flatsP)

    # Get Patches (building blocks for wall textures)
    patchesNames = getPatchesNames(iData.getLump("PNAMES"))
    patches = getPictures(iData, patchesNames, pallete)
    if pWAD is not None:
        patchesNamesP = getPatchesNames(pData.getLump("PNAMES"))
        # If there is no PNAMES lump,
        # we still want to check for the patches, in case they are
        # redefined in the pWAD
        if len(patchesNamesP) == 0:
            patchesNamesP = patchesNames
        patchesP = getPictures(pData, patchesNamesP, pallete)
        patchesNames = patchesNamesP
        patches.update(patchesP)

    # Get Textures
    textureInfo = getTextureInfo(iData.getLump("TEXTURE1")) +\
                  getTextureInfo(iData.getLump("TEXTURE2"))
    textures = getTextures(textureInfo, patches, patchesNames)
    if pWAD is not None:
        textureInfoP = getTextureInfo(pData.getLump("TEXTURE1")) +\
                      getTextureInfo(pData.getLump("TEXTURE2"))
        if len(textureInfoP) > 0:
            texturesP = getTextures(textureInfoP, patches, patchesNames)
            textures.update(texturesP)
        # Extra textures that are not found in usual TEXTUREs
        # Most probably they are Picture-as-Textures
        requiredTextures = getListOfTextures(walls)
        additionalTextures = getPictures(pData, requiredTextures, pallete)
        textures.update(additionalTextures)

    # in case pWAD has different names for sprites
    # (for example it does not combine L and R)
    # we need the fullest list of options
    joinedInfoTable = iData.infoTable.copy()
    if pWAD is not None: 
        joinedInfoTable += pData.infoTable.copy()

    # Get things / sprites
    thingsList, spriteList = [], []
    sprites = {}
    thingsList, spriteList = parceThings(things, joinedInfoTable, options, stats)
    sprites = getPictures(iData, spriteList, pallete)

    # Update things / sprites from pWAD
    if pWAD is not None:
        if thingsList == [] and spriteList == []:
            thingsList, spriteList = \
                    parceThings(things, joinedInfoTable, options, stats)
        spritesP = getPictures(pData, spriteList, pallete)
        sprites.update(spritesP)

    # Generate Color Conversion table
    # (Color mapping for different light levels)
    colorConversion = genColorConversion(pallete, colorMap)

    if options["verbose"]:
        print ("Getting assets: Done")
        print (f"Stat: {len(flats)} flt, {len(patches)} pch, " +
               f"{len(textures)} txt, {len(walls)} wls, " +
               f"{len(thingsList)} thg, {len(sprites)} spr")

    # Draw the picture
    im = drawMap(vertexes, linedefs, sidedefs, sectors, flats, walls, textures,
                 thingsList, sprites, colorConversion, options)

    # Apply gamma correction to the final picture
    # It usually is a bit dark without it
    if options["gamma"] != 1:
        gammaCorrection(im, options["gamma"])

    # Clean WAD names (for statistic)
    iWADName = iWAD
    if "/" in iWAD:
        iWADName = iWAD.split("/")[-1]
        stats["01iWAD"] = iWADName

    if pWAD is not None:
        pWADName = pWAD
        if "/" in pWAD:
            pWADName = pWAD.split("/")[-1]
            stats["02pWAD"] = pWADName
    # This one is for the filename of the resulting image
    wadName = iWADName if pWAD is None else pWADName

    # Level Geometry statistics
    stats["03Map"] = mapName
    stats["11Vertexes"] = len(vertexes)
    stats["12Linedefs"] = len(linedefs)
    stats["13Sidedefs"] = len(sidedefs)
    stats["14Sectors"] = len(sectors)
    stats["15Things"] = len(things)

    # Get TitlePic
    titlepic = getPicture(iData.getLump("TITLEPIC"), pallete)
    if pWAD is not None:
        titlepic = getPicture(pData.getLump("TITLEPIC"), pallete)

    # Draw/write statistics info in the final image
    drawStats(im, titlepic, stats)

    # Save the image
    im.save(wadName.split(".")[0]+"-"+mapName+".png")

    return True


# This is the public function, that wraps the basic map drawing function
# It mainly generates list of maps for "ALL" option
# and set default options
def wad2pic(iWAD, mapName=None, pWAD=None, options={}):

    # Wrap the whole thing in one big try-except,
    # so it will not stop at one broken map,
    # when you generate "ALL" maps
    def genMapWithException(iWAD, mapName, pWAD, options):

        # When debug is on: just run the function
        if options["debug"]:
            if generateMapPic(iWAD, options, mapName, pWAD):
                if options["verbose"]:
                    print ("Generated map:", iWAD, mapName, pWAD)
            return

        # If debug is off, in case of error:
        # just displate error message and move on
        try:
            if generateMapPic(iWAD, options, mapName, pWAD):
                if options["verbose"]:
                    print ("Generated map:", iWAD, mapName, pWAD)
        except:
            print ("Error while generating map:", iWAD, mapName, pWAD)

    # Settings' defaults
    if "margins" not in options:
        options["margins"] = 300
    if "gamma" not in options:
        options["gamma"] = .7
    if "coefX" not in options:
        options["coefX"] = 0
    if "coefY" not in options:
        options["coefY"] = .8
    if "rotate" not in options:
        options["rotate"] = 30
    if "scaleY" not in options:
        options["scaleY"] = 0.8
    if "zStyle" not in options:
        options["zStyle"] = False
    if "verbose" not in options:
        options["verbose"] = True
    if "debug" not in options:
        options["debug"] = False

    # List of all possible map names (if "ALL"
    listOfMapNames = [mapName]
    if mapName is None or mapName.upper() == "ALL":
        listOfMapNames = ["E" + str(i) + "M" + str(j) for i in range(1, 5)
                            for j in range(1, 10)] + \
                         ["MAP" + str(i).zfill(2) for i in range(1, 33)]

    # generate the map for each map name
    for mapName in listOfMapNames:
        genMapWithException(iWAD, mapName, pWAD, options)


# Test if the IWAD and PWAD files exist.
def testFilesExist(options):

    # Test IWAD exists
    filename = options["<iwad>"]
    exists = os.path.isfile(filename)
    if not exists:
        print("IWAD not found: %s" % (filename))
        return exists
    
    # Test PWAD exists (if given)
    if options["<pwad>"] is not None:
        filename = options["<pwad>"]
        exists = os.path.isfile(filename)
        if not exists:
            print("PWAD not found: %s" % (filename))
            return exists
    
    return True


def printLicense(options):

    if options["--license"] == True:
        print(constants.LICENSE)
        return True


# Converts the program command arguments to wad2pic options format.
# This keeps the internal option names backward compatible, it also
# casts ints and floats to their correct data type.
def convertDocOptions(options):

    return {
        "margins": int(options["--margin"]),
        "gamma"  : float(options["--gamma"]),
        "coefX"  : float(options["--coefx"]),
        "coefY"  : float(options["--coefy"]),
        "rotate": int(options["--rotate"]),
        "scaleY": float(options["--iso"]),
        "zStyle": options["--zstyle"],
        "verbose" : not options["--quiet"],
        "debug" : options["--debug"]
      }


if __name__ == "__main__":
    
    # If called directly, assume this is a CLI usage case
    # CLI usage works like this:

    # Parse command line arguments.
    docOptions = docopt(__doc__, version=constants.VERSION)
    if isinstance(docOptions, dict):
        if docOptions["--debug"]:
            # Print the parsed command line arguments
            print(docOptions)
        if printLicense(docOptions):
            sys.exit(0)
        elif not testFilesExist(docOptions):
            # Return non-zero exit code to conform to POSIX standard
            # for when an error occured.
            sys.exit(255)
        else:
            iwad = docOptions["<iwad>"]
            pwad = docOptions["<pwad>"]
            map = docOptions["<map>"]
            options = convertDocOptions(docOptions)
            wad2pic(iwad, map, pwad, options)
    else:
        # Print usage text
        print(docOptions)
