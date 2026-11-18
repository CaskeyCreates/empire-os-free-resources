# Test fixtures

`face.jpg` is a public-domain portrait used by the face detector test.

- Subject: official NASA portrait of astronaut Laurel B. Clark, 960 x 1306 px (a downsized copy).
- Source: https://commons.wikimedia.org/wiki/File:Laurel_Clark,_NASA_photo_portrait_in_blue_suit.jpg
- Original: NASA, https://science.ksc.nasa.gov/shuttle/missions/sts-107/images/high/KSC-02PD-1055.jpg
- Licence: public domain (a work of NASA, a US federal agency), as Wikimedia Commons states.

The test measured the face by hand as fractions of the frame (x 0.370, y 0.233, w 0.235, h 0.226,
forehead to chin and cheek to cheek) and accepts a detected box within 0.10 of each.
