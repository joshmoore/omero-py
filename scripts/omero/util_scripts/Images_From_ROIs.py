"""
 components/tools/OmeroPy/scripts/omero/util_scripts/Images_From_ROIs.py

-----------------------------------------------------------------------------
  Copyright (C) 2006-2010 University of Dundee. All rights reserved.


  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  
  You should have received a copy of the GNU General Public License along
  with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

------------------------------------------------------------------------------

This script gets all the Rectangles from a particular image, then creates new images with 
the regions within the ROIs, and saves them back to the server.
    
@author  Will Moore &nbsp;&nbsp;&nbsp;&nbsp;
<a href="mailto:will@lifesci.dundee.ac.uk">will@lifesci.dundee.ac.uk</a>
@version 3.0
<small>
(<b>Internal version:</b> $Revision: $Date: $)
</small>
@since 3.0-Beta4.2
 
"""

import omero
import omero.scripts as scripts
import omero_api_IRoi_ice
from omero.rtypes import *
import omero.util.script_utils as scriptUtil

import os
import numpy

import time
startTime = 0

def printDuration(output=True):
    global startTime
    if startTime == 0:
        startTime = time.time()
    if output:
        print "Script timer = %s secs" % (time.time() - startTime)

def getRectangles(session, imageId):
    """ Returns a list of (x, y, width, height) of each rectange ROI in the image """
    
    rectangles = []
    shapes = []        # string set. 
    
    roiService = session.getRoiService()
    result = roiService.findByImage(imageId, None)
    
    rectCount = 0
    for roi in result.rois:
        for shape in roi.copyShapes():
            if type(shape) == omero.model.RectI:
                x = shape.getX().getValue()
                y = shape.getY().getValue()
                width = shape.getWidth().getValue()
                height = shape.getHeight().getValue()
                rectangles.append((int(x), int(y), int(width), int(height)))
                continue
    return rectangles


def getImagePlane(queryService, rawPixelStore, imageId):
    """    Gets the first plane from the specified image and returns it as a numpy array. """

    # get pixels with pixelsType
    query_string = "select p from Pixels p join fetch p.image i join fetch p.pixelsType pt where i.id='%d'" % imageId
    pixels = queryService.findByQuery(query_string, None)
    theX = pixels.getSizeX().getValue()
    theY = pixels.getSizeY().getValue()

    # get the plane
    theZ, theC, theT = (0,0,0)
    pixelsId = pixels.getId().getValue()
    bypassOriginalFile = True
    rawPixelStore.setPixelsId(pixelsId, bypassOriginalFile)
    plane2D = scriptUtil.downloadPlane(rawPixelStore, pixels, theZ, theC, theT)
    
    return plane2D
    

def makeImagesFromRois(session, parameterMap):
    """
    Processes the list of Image_IDs, either making a new image-stack or a new dataset from each image,
    with new image planes coming from the regions in Rectangular ROIs on the parent images. 
    """
    
    imageIds = []
    dataType = parameterMap["Data_Type"]
    ids = parameterMap["IDs"]
    
    containerService = session.getContainerService()
    
    # images to export
    images = containerService.getImages(dataType, ids, None)
    for image in images:
        imageIds.append(image.getId().getValue())
    
    if len(imageIds) == 0:
        return "No image-IDs in list."
        
    print "Processing Image IDs: ", imageIds
    
    queryService = session.getQueryService()
    rawPixelStore = session.createRawPixelsStore()
    updateService = session.getUpdateService()
    
    # get the project and dataset from the first image, to use as containers for new Images / Datasets
    imageId = imageIds[0]
    query_string = "select i from Image i join fetch i.datasetLinks idl join fetch idl.parent d join fetch d.projectLinks pl join fetch pl.parent where i.id in (%s)" % imageId
    image = queryService.findByQuery(query_string, None)
    project = None
    dataset = None
    if image:
        print "Ancestors of first Image (used for new Images/Datsets):"
        for link in image.iterateDatasetLinks():
            dataset = link.parent
            # dataset = queryService.get("Dataset", ds.id.val)
            print "  Dataset: ", dataset.name.val
            for dpLink in dataset.iterateProjectLinks():
                project = dpLink.parent
                print "  Project: ", project.name.val
                break # only use 1st Project
            break    # only use 1st
    else:
        print "No Project and Dataset found for Image ID: %s" % imageId
    
    containerName = 'From_ROIs'
    if "Container_Name" in parameterMap:
        containerName = parameterMap["Container_Name"]
    
    newDataset = None    # only created if not putting images in stack
    imageStack = ("Make_Image_Stack" in parameterMap) and (parameterMap["Make_Image_Stack"])
    newIds = []
    for imageId in imageIds:
    
        iList = containerService.getImages("Image", [imageId], None)
        if len(iList) == 0:
            print "Image ID: %s not found." % imageId
            continue
        image = iList[0]
        imageName = image.getName().getValue()
    
        pixels = image.getPrimaryPixels()
        # note pixel sizes (if available) to set for the new images
        physicalSizeX = pixels.getPhysicalSizeX() and pixels.getPhysicalSizeX().getValue() or None
        physicalSizeY = pixels.getPhysicalSizeY() and pixels.getPhysicalSizeY().getValue() or None
        
        # get plane of image
        plane2D = getImagePlane(queryService, rawPixelStore, imageId)
    
        # get ROI Rectangles, as (x, y, width, height)
        rects = getRectangles(session, imageId)
        
        # if making a single stack image...
        if imageStack:
            print "\nMaking Image stack from ROIs of Image:", imageId
            print "physicalSize X, Y:  %s, %s" % (physicalSizeX, physicalSizeY)
            plane2Dlist = []
            # use width and height from first rectangle to make sure that all are the same. 
            x,y,width,height = rects[0]    
            for r in rects:
                x,y,w,h = r
                x2 = x+width
                y2 = y+height
                plane2Dlist.append(plane2D[y:y2, x:x2])
            
            newImageName = "%s_%s" % (os.path.basename(imageName), containerName)
        
            description = "Image from ROIS on parent Image:\n  Name: %s\n  Image ID: %d" % (imageName, imageId)
            print description
            image = scriptUtil.createNewImage(session, plane2Dlist, newImageName, description, dataset)
        
            pixels = image.getPrimaryPixels()
            if physicalSizeX:
                pixels.setPhysicalSizeX(rdouble(physicalSizeX))
            if physicalSizeY:
                pixels.setPhysicalSizeY(rdouble(physicalSizeY))
            updateService.saveObject(pixels)
            
            newIds.append(image.getId().getValue())
    
        # ..else, make an image for each ROI (all in one dataset?)
        else:
            iIds = []
            for r in rects:
                x,y,w,h = r
                print "  ROI x: %s y: %s w: %s h: %s" % (x, y, w, h)
                x2 = x+w
                y2 = y+h
                array = plane2D[y:y2, x:x2]     # slice the ROI rectangle data out of the whole image-plane 2D array
            
                description = "Created from image:\n  Name: %s\n  Image ID: %d \n x: %d y: %d" % (imageName, imageId, x, y)
                image = scriptUtil.createNewImage(session, [array], "from_roi", description)
            
                pixels = image.getPrimaryPixels()
                pixels.setPhysicalSizeX(rdouble(physicalSizeX))
                pixels.setPhysicalSizeY(rdouble(physicalSizeY))
                updateService.saveObject(pixels)
                
                iIds.append(image.getId().getValue())
                
            if len(iIds) > 0:
                # create a new dataset for new images
                datasetName = containerName    # e.g. myImage.mrc_particles
                print "\nMaking Dataset '%s' of Images from ROIs of Image: %s" % (datasetName, imageId)
                print "physicalSize X, Y:  %s, %s" % (physicalSizeX, physicalSizeY)
                dataset = omero.model.DatasetI()
                dataset.name = rstring(datasetName)
                desc = "Images in this Dataset are from ROIs of parent Image:\n  Name: %s\n  Image ID: %d" % (imageName, imageId)
                dataset.description = rstring(desc)
                dataset = updateService.saveAndReturnObject(dataset)
                if project:        # and put it in the current project
                    link = omero.model.ProjectDatasetLinkI()
                    link.parent = omero.model.ProjectI(project.id.val, False)
                    link.child = omero.model.DatasetI(dataset.id.val, False)
                    updateService.saveAndReturnObject(link)
                    
                for iid in iIds:
                    link = omero.model.DatasetImageLinkI()
                    link.parent = omero.model.DatasetI(dataset.id.val, False)
                    link.child = omero.model.ImageI(iid, False)
                    session.getUpdateService().saveObject(link)
                newIds.append(dataset.getId().getValue())
                newDataset = dataset

    plural = (len(newIds) == 1) and "." or "s."
    if imageStack:
        message = "Created %s new Image%s Refresh Dataset to view" % (len(newIds), plural)
    else:
        message = "Created %s new Dataset%s Refresh Project to view" % (len(newIds), plural)
    return (message, newDataset)

def runAsScript():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters. 
    """
    printDuration(False)    # start timer
    dataTypes = [rstring('Dataset'),rstring('Image')]
    
    client = scripts.client('Images_From_ROIs.py', """Create new Images from the regions defined by Rectangle ROIs on other Images.
Designed to work with single-plane images (Z=1 T=1) with multiple ROIs per image. 
If you choose to make an image stack from all the ROIs, this script
assumes that all the ROIs on each Image are the same size.""",

    scripts.String("Data_Type", optional=False, grouping="1",
        description="Choose Images via their 'Dataset' or directly by 'Image' IDs.", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="2",
        description="List of Dataset IDs or Image IDs to process.").ofType(rlong(0)),
        
    scripts.String("Container_Name", grouping="3",
        description="New Dataset name or Image name (if 'Make_Image_Stack')", default="From_ROIs"),
        
    scripts.Bool("Make_Image_Stack", grouping="4",
        description="If true, make a single Image (stack) from all the ROIs of each parent Image"),
        
    version = "4.2.0",
    authors = ["William Moore", "OME Team"],
    institutions = ["University of Dundee"],
    contact = "ome-users@lists.openmicroscopy.org.uk",
    )
    
    try:
        session = client.getSession();

        # process the list of args above.
        parameterMap = {}
        for key in client.getInputKeys():
            if client.getInput(key):
                parameterMap[key] = unwrap( client.getInput(key).getValue() )

        print parameterMap

        message, newDataset = makeImagesFromRois(session, parameterMap)

        if message:
            client.setOutput("Message", rstring(message))
        else:
            client.setOutput("Message", rstring("Script Failed. See 'error' or 'info'"))
            
        if newDataset:
            client.setOutput("Dataset", robject(newDataset))
    finally:
        client.closeSession()
        printDuration()

if __name__ == "__main__":
    runAsScript()