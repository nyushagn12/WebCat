# WebCat
A browser engine, using Tkinter and QuickJS.

## What is WebCat, descriptively?
WebCat is a browser engine that supports HTML+CSS+JS, but translates HTML+CSS into Tkinter, and JS is ran by QuickJS (actually, its fork, quickjs-ng).

## What is WebCat trying to achieve?
WebCat's main goal is security. Most engines, like Blink, WebKit, Gecko and more, have lots of vulnerabilities. I believe that QuickJS and Tkinter is good for this mission. 

## Why wouldn't you just disable JS?
Try it for yourself. Disable JS. Search engines don't work. YouTube doesn't works. No social media app works. That's safe, but boring. The plan is to have the best of both worlds.

## Is it ready to use?
I am gonna be honest with you:
### No.
It's still in development. You can browse JavaScript pages, but JavaScript here ain't really finished. Google waits for a redirect to happen, YouTube renders but JavaScript fails "JavaScript message is too large", and it's slow (working on making it faster, of course it won't be slow forever)

## What does it uses?
It obviously uses Python (since Tkinter and QuickJS are python modules (I know that I've repeated tkinter and quickjs too much)). To install them, do:
```pip install quickjs-ng```
You don't need Tkinter. It's already built into Python.

## How do I run it?
It's really simple to launch. Run launch.py in the downloaded folder.

## How do I download it?
Clone it and unzip it.

## May I help?
Of course you can. All suggestions/contributions are welcomed.
