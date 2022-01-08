from distutils.core import setup
import py2exe
setup(console=['wad2pic.py'],options={"py2exe":{"dist_dir":"dist"}})
