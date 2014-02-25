import os
import sys
import math
import vtk
import colorsys
import time
import functools
import traceback
import PythonQt
from PythonQt import QtCore, QtGui
import ddapp.applogic as app
from ddapp import objectmodel as om
from ddapp import perception
from ddapp import lcmUtils
from ddapp import transformUtils
from ddapp.transformUtils import getTransformFromAxes
from ddapp.timercallback import TimerCallback
from ddapp import mapsregistrar
from ddapp.visualization import *

import numpy as np
import vtkNumpy
from debugVis import DebugData
from shallowCopy import shallowCopy
import affordance
import ioUtils
import pointCloudUtils

import vtkPCLFiltersPython as pcl

import drc as lcmdrc
import bot_core as lcmbotcore

import vs as lcmvs
from ddapp import lcmUtils


DRILL_TRIANGLE_BOTTOM_LEFT = 'bottom left'
DRILL_TRIANGLE_BOTTOM_RIGHT = 'bottom right'
DRILL_TRIANGLE_TOP_LEFT = 'top left'
DRILL_TRIANGLE_TOP_RIGHT = 'top right'


def getSegmentationView():
    return app.getViewManager().findView('Segmentation View')


def getDRCView():
    return app.getViewManager().findView('DRC View')


def switchToView(viewName):
    app.getViewManager().switchToView(viewName)


def getCurrentView():
    return app.getViewManager().currentView()


def getDebugFolder():
    obj = om.findObjectByName('debug')
    if obj is None:
        obj = om.getOrCreateContainer('debug', om.getOrCreateContainer('segmentation'))
        om.collapse(obj)
    return obj


def thresholdPoints(polyData, arrayName, thresholdRange):
    assert(polyData.GetPointData().GetArray(arrayName))
    f = vtk.vtkThresholdPoints()
    f.SetInput(polyData)
    f.ThresholdBetween(thresholdRange[0], thresholdRange[1])
    f.SetInputArrayToProcess(0,0,0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, arrayName)
    f.Update()
    return shallowCopy(f.GetOutput())


def transformPolyData(polyData, transform):

    t = vtk.vtkTransformPolyDataFilter()
    t.SetTransform(transform)
    t.SetInput(shallowCopy(polyData))
    t.Update()
    return shallowCopy(t.GetOutput())


def cropToLineSegment(polyData, point1, point2):

    line = point2 - point1
    length = np.linalg.norm(line)
    axis = line / length

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, axis, origin=point1, resultArrayName='dist_along_line')
    return thresholdPoints(polyData, 'dist_along_line', [0.0, length])



'''
icp programmable filter

import vtkFiltersGeneralPython as filtersGeneral

points = inputs[0]
block = inputs[1]

print points.GetNumberOfPoints()
print block.GetNumberOfPoints()

if points.GetNumberOfPoints() < block.GetNumberOfPoints():
    block, points = points, block

icp = vtk.vtkIterativeClosestPointTransform()
icp.SetSource(points.VTKObject)
icp.SetTarget(block.VTKObject)
icp.GetLandmarkTransform().SetModeToRigidBody()
icp.Update()

t = filtersGeneral.vtkTransformPolyDataFilter()
t.SetInput(points.VTKObject)
t.SetTransform(icp)
t.Update()

output.ShallowCopy(t.GetOutput())
'''



def lockAffordanceToHand(aff, hand='l_hand'):

    linkFrame = getLinkFrame(hand)
    affT = aff.actor.GetUserTransform()

    if not hasattr(aff, 'handToAffT') or not aff.handToAffT:
        aff.handToAffT = computeAToB(linkFrame, affT)

    t = vtk.vtkTransform()
    t.PostMultiply()
    t.Concatenate(aff.handToAffT)
    t.Concatenate(linkFrame)
    aff.actor.GetUserTransform().SetMatrix(t.GetMatrix())
    aff.publish()


handAffUpdater = TimerCallback()
handAffUpdater.targetFps = 30
handAffUpdater.callback = None


def lockToHandOn():
    aff = getDefaultAffordanceObject()
    if not aff:
        return
    handAffUpdater.callback = functools.partial(lockAffordanceToHand, aff)
    handAffUpdater.start()


def lockToHandOff():

    aff = getDefaultAffordanceObject()
    if not aff:
        return

    handAffUpdater.stop()
    aff.handToAffT = None


def getRandomColor():
    '''
    Return a random color as a list of RGB values between 0.0 and 1.0.
    '''
    return colorsys.hsv_to_rgb(np.random.rand(), 1.0, 0.9)



def extractLargestCluster(polyData, minClusterSize=100):

    polyData = applyEuclideanClustering(polyData, minClusterSize=minClusterSize)
    return thresholdPoints(polyData, 'cluster_labels', [1, 1])


def extractClusters(polyData, **kwargs):

    if not polyData.GetNumberOfPoints():
        return []

    polyData = applyEuclideanClustering(polyData, **kwargs)
    clusterLabels = vtkNumpy.getNumpyFromVtk(polyData, 'cluster_labels')
    clusters = []
    for i in xrange(1, clusterLabels.max() + 1):
        cluster = thresholdPoints(polyData, 'cluster_labels', [i, i])
        clusters.append(cluster)
    return clusters



def segmentGroundPoints(polyData):

    zvalues = vtkNumpy.getNumpyFromVtk(polyData, 'Points')[:,2]
    groundHeight = np.percentile(zvalues, 5)
    polyData = thresholdPoints(polyData, 'z', [groundHeight - 0.3, groundHeight + 0.3])

    polyData, normal = applyPlaneFit(polyData, distanceThreshold=0.005, expectedNormal=[0,0,1])
    groundPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])

    return groundPoints, normal


def segmentGroundPlane():

    inputObj = om.findObjectByName('pointcloud snapshot')
    inputObj.setProperty('Visible', False)
    polyData = shallowCopy(inputObj.polyData)

    zvalues = vtkNumpy.getNumpyFromVtk(polyData, 'Points')[:,2]
    groundHeight = np.percentile(zvalues, 5)
    searchRegion = thresholdPoints(polyData, 'z', [groundHeight - 0.3, groundHeight + 0.3])

    updatePolyData(searchRegion, 'ground search region', parent=getDebugFolder(), colorByName='z', visible=False)

    _, origin, normal = applyPlaneFit(searchRegion, distanceThreshold=0.02, expectedNormal=[0,0,1], perpendicularAxis=[0,0,1], returnOrigin=True)

    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    dist = np.dot(points - origin, normal)
    vtkNumpy.addNumpyToVtk(polyData, dist, 'dist_to_plane')

    groundPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    scenePoints = thresholdPoints(polyData, 'dist_to_plane', [0.05, 10])

    updatePolyData(groundPoints, 'ground points', alpha=0.3)
    updatePolyData(scenePoints, 'scene points', alpha=0.3)

    #scenePoints = applyEuclideanClustering(scenePoints, clusterTolerance=0.10, minClusterSize=100, maxClusterSize=1e6)
    #updatePolyData(scenePoints, 'scene points', colorByName='cluster_labels')


def getMajorPlanes(polyData, useVoxelGrid=True):

    voxelGridSize = 0.01
    distanceToPlaneThreshold = 0.02

    if useVoxelGrid:
        polyData = applyVoxelGrid(polyData, leafSize=voxelGridSize)

    polyDataList = []

    minClusterSize = 100

    while len(polyDataList) < 25:

        polyData, normal = applyPlaneFit(polyData, distanceToPlaneThreshold)
        outliers = thresholdPoints(polyData, 'ransac_labels', [0, 0])
        inliers = thresholdPoints(polyData, 'ransac_labels', [1, 1])
        largestCluster = extractLargestCluster(inliers)

        #i = len(polyDataList)
        #showPolyData(inliers, 'inliers %d' % i, color=getRandomColor(), parent='major planes')
        #showPolyData(outliers, 'outliers %d' % i, color=getRandomColor(), parent='major planes')
        #showPolyData(largestCluster, 'cluster %d' % i, color=getRandomColor(), parent='major planes')

        if largestCluster.GetNumberOfPoints() > minClusterSize:
            polyDataList.append(largestCluster)
            polyData = outliers
        else:
            break

    return polyDataList


def showMajorPlanes():

    inputObj = om.findObjectByName('pointcloud snapshot')
    inputObj.setProperty('Visible', False)
    polyData = inputObj.polyData

    om.removeFromObjectModel(om.findObjectByName('major planes'))
    folderObj = om.findObjectByName('segmentation')
    folderObj = om.getOrCreateContainer('major planes', folderObj)

    polyData = thresholdPoints(polyData, 'distance', [1, 4])
    polyDataList = getMajorPlanes(polyData)


    for i, polyData in enumerate(polyDataList):
        obj = showPolyData(polyData, 'plane %d' % i, color=getRandomColor(), visible=True, parent='major planes')
        obj.setProperty('Point Size', 3)


def cropToBox(polyData, params, expansionDistance=0.1):


    origin = params['origin']

    xwidth = params['xwidth']
    ywidth = params['ywidth']
    zwidth = params['zwidth']

    xaxis = params['xaxis']
    yaxis = params['yaxis']
    zaxis = params['zaxis']


    for axis, width in ((xaxis, xwidth), (yaxis, ywidth), (zaxis, zwidth)):
        cropAxis = axis*(width/2.0 + expansionDistance)
        polyData = cropToLineSegment(polyData, origin - cropAxis, origin + cropAxis)

    updatePolyData(polyData, 'cropped')


def cropToSphere(polyData, origin, radius):
    polyData = labelDistanceToPoint(polyData, origin)
    return thresholdPoints(polyData, 'distance_to_point', [0, radius])


def applyEuclideanClustering(dataObj, clusterTolerance=0.05, minClusterSize=100, maxClusterSize=1e6):

    f = pcl.vtkPCLEuclideanClusterExtraction()
    f.SetInput(dataObj)
    f.SetClusterTolerance(clusterTolerance)
    f.SetMinClusterSize(int(minClusterSize))
    f.SetMaxClusterSize(int(maxClusterSize))
    f.Update()
    return shallowCopy(f.GetOutput())


def labelOutliers(dataObj, searchRadius=0.03, neighborsInSearchRadius=10):

    f = pcl.vtkPCLRadiusOutlierRemoval()
    f.SetInput(dataObj)
    f.SetSearchRadius(searchRadius)
    f.SetNeighborsInSearchRadius(int(neighborsInSearchRadius))
    f.Update()
    return shallowCopy(f.GetOutput())


def applyPlaneFit(polyData, distanceThreshold=0.02, expectedNormal=None, perpendicularAxis=None, angleEpsilon=0.2, returnOrigin=False, searchOrigin=None, searchRadius=None):

    expectedNormal = expectedNormal if expectedNormal is not None else [-1,0,0]

    fitInput = polyData
    if searchOrigin is not None:
        assert searchRadius
        fitInput = cropToSphere(fitInput, searchOrigin, searchRadius)

    # perform plane segmentation
    f = pcl.vtkPCLSACSegmentationPlane()
    f.SetInput(fitInput)
    f.SetDistanceThreshold(distanceThreshold)
    if perpendicularAxis is not None:
        f.SetPerpendicularConstraintEnabled(True)
        f.SetPerpendicularAxis(perpendicularAxis)
        f.SetAngleEpsilon(angleEpsilon)
    f.Update()
    origin = f.GetPlaneOrigin()
    normal = np.array(f.GetPlaneNormal())

    # flip the normal if needed
    if np.dot(normal, expectedNormal) < 0:
        normal = -normal

    # for each point, compute signed distance to plane

    polyData = shallowCopy(polyData)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    dist = np.dot(points - origin, normal)
    vtkNumpy.addNumpyToVtk(polyData, dist, 'dist_to_plane')

    if returnOrigin:
        return polyData, origin, normal
    else:
        return polyData, normal


def applyLineFit(dataObj, distanceThreshold=0.02):

    f = pcl.vtkPCLSACSegmentationLine()
    f.SetInput(dataObj)
    f.SetDistanceThreshold(distanceThreshold)
    f.Update()
    origin = np.array(f.GetLineOrigin())
    direction = np.array(f.GetLineDirection())

    return origin, direction, shallowCopy(f.GetOutput())


def addCoordArraysToPolyData(polyData):
    polyData = shallowCopy(polyData)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    vtkNumpy.addNumpyToVtk(polyData, points[:,0].copy(), 'x')
    vtkNumpy.addNumpyToVtk(polyData, points[:,1].copy(), 'y')
    vtkNumpy.addNumpyToVtk(polyData, points[:,2].copy(), 'z')

    bodyFrame = perception._multisenseItem.model.getFrame('body')
    bodyOrigin = bodyFrame.TransformPoint([0.0, 0.0, 0.0])
    bodyX = bodyFrame.TransformVector([1.0, 0.0, 0.0])
    bodyY = bodyFrame.TransformVector([0.0, 1.0, 0.0])
    bodyZ = bodyFrame.TransformVector([0.0, 0.0, 1.0])
    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, bodyX, origin=bodyOrigin, resultArrayName='distance_along_robot_x')
    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, bodyY, origin=bodyOrigin, resultArrayName='distance_along_robot_y')
    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, bodyZ, origin=bodyOrigin, resultArrayName='distance_along_robot_z')

    return polyData


def getDebugRevolutionData():
    #dataDir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../drc-data'))
    #filename = os.path.join(dataDir, 'valve_wall.vtp')
    #filename = os.path.join(dataDir, 'bungie_valve.vtp')
    #filename = os.path.join(dataDir, 'cinder-blocks.vtp')
    #filename = os.path.join(dataDir, 'cylinder_table.vtp')
    #filename = os.path.join(dataDir, 'firehose.vtp')
    #filename = os.path.join(dataDir, 'debris.vtp')
    #filename = os.path.join(dataDir, 'rev1.vtp')
    #filename = os.path.join(dataDir, 'drill-in-hand.vtp')

    filename = os.path.expanduser('~/Desktop/scans/debris-scan.vtp')

    return addCoordArraysToPolyData(ioUtils.readPolyData(filename))


def getCurrentRevolutionData():
    revPolyData = perception._multisenseItem.model.revPolyData
    if not revPolyData or not revPolyData.GetNumberOfPoints():
        return None

    if useVoxelGrid:
        revPolyData = applyVoxelGrid(revPolyData, leafSize=0.015)

    return addCoordArraysToPolyData(revPolyData)


def getCurrentMapServerData():
    mapServer = om.findObjectByName('Map Server')
    polyData = None
    if mapServer and mapServer.getProperty('Visible'):
        polyData = mapServer.source.polyData

    if not polyData or not polyData.GetNumberOfPoints():
        return None

    return addCoordArraysToPolyData(polyData)


useVoxelGrid = False

def applyVoxelGrid(polyData, leafSize=0.01):

    v = pcl.vtkPCLVoxelGrid()
    v.SetLeafSize(leafSize, leafSize, leafSize)
    v.SetInput(polyData)
    v.Update()
    return shallowCopy(v.GetOutput())



def segmentGroundPlanes():

    objs = []
    for obj in om.objects.values():
        name = obj.getProperty('Name')
        if name.startswith('pointcloud snapshot'):
            objs.append(obj)

    objs = sorted(objs, key=lambda x: x.getProperty('Name'))

    d = DebugData()

    prevHeadAxis = None
    for obj in objs:
        name = obj.getProperty('Name')
        print '----- %s---------' % name
        print  'head axis:', obj.headAxis
        groundPoints, normal = segmentGroundPoints(obj.polyData)
        print 'ground normal:', normal
        showPolyData(groundPoints, name + ' ground points', visible=False)
        a = np.array([0,0,1])
        b = np.array(normal)
        diff = math.degrees(math.acos(np.dot(a,b) / (np.linalg.norm(a) * np.linalg.norm(b))))
        if diff > 90:
            print 180 - diff
        else:
            print diff

        if prevHeadAxis is not None:
            a = prevHeadAxis
            b = np.array(obj.headAxis)
            diff = math.degrees(math.acos(np.dot(a,b) / (np.linalg.norm(a) * np.linalg.norm(b))))
            if diff > 90:
                print 180 - diff
            else:
                print diff
        prevHeadAxis = np.array(obj.headAxis)

        d.addLine([0,0,0], normal)

    updatePolyData(d.getPolyData(), 'normals')


def extractCircle(polyData, distanceThreshold=0.04, radiusLimit=None):

    circleFit = pcl.vtkPCLSACSegmentationCircle()
    circleFit.SetDistanceThreshold(distanceThreshold)
    circleFit.SetInput(polyData)
    if radiusLimit is not None:
        circleFit.SetRadiusLimit(radiusLimit)
        circleFit.SetRadiusConstraintEnabled(True)
    circleFit.Update()

    polyData = thresholdPoints(circleFit.GetOutput(), 'ransac_labels', [1.0, 1.0])
    return polyData, circleFit


def removeMajorPlane(polyData, distanceThreshold=0.02):

    # perform plane segmentation
    f = pcl.vtkPCLSACSegmentationPlane()
    f.SetInput(polyData)
    f.SetDistanceThreshold(distanceThreshold)
    f.Update()

    polyData = thresholdPoints(f.GetOutput(), 'ransac_labels', [0.0, 0.0])
    return polyData, f


def removeGround(polyData, groundThickness=0.02, sceneHeightFromGround=0.05):

    searchRegionThickness = 0.5

    zvalues = vtkNumpy.getNumpyFromVtk(polyData, 'Points')[:,2]
    groundHeight = np.percentile(zvalues, 5)

    vtkNumpy.addNumpyToVtk(polyData, zvalues.copy(), 'z')
    searchRegion = thresholdPoints(polyData, 'z', [groundHeight - searchRegionThickness/2.0, groundHeight + searchRegionThickness/2.0])

    updatePolyData(searchRegion, 'ground search region', parent=getDebugFolder(), colorByName='z', visible=False)

    _, origin, normal = applyPlaneFit(searchRegion, distanceThreshold=0.02, expectedNormal=[0,0,1], perpendicularAxis=[0,0,1], returnOrigin=True)

    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    dist = np.dot(points - origin, normal)
    vtkNumpy.addNumpyToVtk(polyData, dist, 'dist_to_plane')

    groundPoints = thresholdPoints(polyData, 'dist_to_plane', [-groundThickness/2.0, groundThickness/2.0])
    scenePoints = thresholdPoints(polyData, 'dist_to_plane', [sceneHeightFromGround, 100])

    return groundPoints, scenePoints


def generateFeetForValve():

    aff = om.findObjectByName('valve affordance')
    assert aff


    params = aff.params

    origin = np.array(params['origin'])
    origin[2] = 0.0

    xaxis = -params['axis']
    zaxis = np.array([0,0,1])
    yaxis = np.cross(zaxis, xaxis)
    xaxis = np.cross(yaxis, zaxis)

    stanceWidth = 0.2
    stanceRotation = 25.0
    stanceOffset = [-1.0, -0.5, 0.0]

    valveFrame = getTransformFromAxes(xaxis, yaxis, zaxis)
    valveFrame.PostMultiply()
    valveFrame.Translate(origin)

    stanceFrame, lfootFrame, rfootFrame = getFootFramesFromReferenceFrame(valveFrame, stanceWidth, stanceRotation, stanceOffset)

    showFrame(boardFrame, 'board ground frame', parent=aff, scale=0.15, visible=False)
    showFrame(lfootFrame, 'lfoot frame', parent=aff, scale=0.15)
    showFrame(rfootFrame, 'rfoot frame', parent=aff, scale=0.15)

    #d = DebugData()
    #d.addLine(valveFrame.GetPosition(), stanceFrame.GetPosition())
    #updatePolyData(d.getPolyData(), 'stance debug')
    #publishSteppingGoal(lfootFrame, rfootFrame)


def generateFeetForDebris():

    aff = om.findObjectByName('board A')
    if not aff:
        return

    params = aff.params

    origin = np.array(params['origin'])

    origin = origin + params['zaxis']*params['zwidth']/2.0 - params['xaxis']*params['xwidth']/2.0
    origin[2] = 0.0

    yaxis = params['zaxis']
    zaxis = np.array([0,0,1])
    xaxis = np.cross(yaxis, zaxis)

    stanceWidth = 0.35
    stanceRotation = 0.0
    stanceOffset = [-0.48, -0.08, 0]

    boardFrame = getTransformFromAxes(xaxis, yaxis, zaxis)
    boardFrame.PostMultiply()
    boardFrame.Translate(origin)

    stanceFrame, lfootFrame, rfootFrame = getFootFramesFromReferenceFrame(boardFrame, stanceWidth, stanceRotation, stanceOffset)

    showFrame(boardFrame, 'board ground frame', parent=aff, scale=0.15, visible=False)
    lfoot = showFrame(lfootFrame, 'lfoot frame', parent=aff, scale=0.15)
    rfoot = showFrame(rfootFrame, 'rfoot frame', parent=aff, scale=0.15)

    for obj in [lfoot, rfoot]:
        obj.addToView(app.getDRCView())

    #d = DebugData()
    #d.addLine(valveFrame.GetPosition(), stanceFrame.GetPosition())
    #updatePolyData(d.getPolyData(), 'stance debug')
    #publishSteppingGoal(lfootFrame, rfootFrame)


def generateFeetForWye():

    aff = om.findObjectByName('wye points')
    if not aff:
        return

    params = aff.params

    origin = np.array(params['origin'])
    origin[2] = 0.0

    yaxis = params['xaxis']
    xaxis = -params['zaxis']
    zaxis = np.cross(xaxis, yaxis)

    stanceWidth = 0.20
    stanceRotation = 0.0
    stanceOffset = [-0.48, -0.08, 0]

    affGroundFrame = getTransformFromAxes(xaxis, yaxis, zaxis)
    affGroundFrame.PostMultiply()
    affGroundFrame.Translate(origin)

    stanceFrame, lfootFrame, rfootFrame = getFootFramesFromReferenceFrame(affGroundFrame, stanceWidth, stanceRotation, stanceOffset)

    showFrame(affGroundFrame, 'affordance ground frame', parent=aff, scale=0.15, visible=False)
    lfoot = showFrame(lfootFrame, 'lfoot frame', parent=aff, scale=0.15)
    rfoot = showFrame(rfootFrame, 'rfoot frame', parent=aff, scale=0.15)

    for obj in [lfoot, rfoot]:
        obj.addToView(app.getDRCView())

    publishStickyFeet(lfootFrame, rfootFrame)


def getFootFramesFromReferenceFrame(referenceFrame, stanceWidth, stanceRotation, stanceOffset):

    footHeight=0.0745342

    ref = vtk.vtkTransform()
    ref.SetMatrix(referenceFrame.GetMatrix())

    stanceFrame = vtk.vtkTransform()
    stanceFrame.PostMultiply()
    stanceFrame.RotateZ(stanceRotation)
    stanceFrame.Translate(stanceOffset)
    stanceFrame.Concatenate(ref)

    lfootFrame = vtk.vtkTransform()
    lfootFrame.PostMultiply()
    lfootFrame.Translate(0, stanceWidth/2.0, footHeight)
    lfootFrame.Concatenate(stanceFrame)

    rfootFrame = vtk.vtkTransform()
    rfootFrame.PostMultiply()
    rfootFrame.Translate(0, -stanceWidth/2.0, footHeight)
    rfootFrame.Concatenate(stanceFrame)

    return stanceFrame, lfootFrame, rfootFrame


def poseFromFrame(frame):

    trans = lcmdrc.vector_3d_t()
    trans.x, trans.y, trans.z = frame.GetPosition()

    wxyz = range(4)
    perception.drc.vtkMultisenseSource.GetBotQuaternion(frame, wxyz)
    quat = lcmdrc.quaternion_t()
    quat.w, quat.x, quat.y, quat.z = wxyz

    pose = lcmdrc.position_3d_t()
    pose.translation = trans
    pose.rotation = quat
    return pose


def publishStickyFeet(lfootFrame, rfootFrame):

    worldAffordanceId = affordance.publishWorldAffordance()

    m = lcmdrc.traj_opt_constraint_t()
    m.utime = int(time.time() * 1e6)
    m.robot_name = worldAffordanceId
    m.num_links = 2
    m.link_name = ['l_foot', 'r_foot']
    m.link_timestamps = [m.utime, m.utime]
    m.num_joints = 0
    m.link_origin_position = [poseFromFrame(lfootFrame), poseFromFrame(rfootFrame)]
    #lcmUtils.publish('DESIRED_FOOT_STEP_SEQUENCE', m)
    lcmUtils.publish('AFF_TRIGGERED_CANDIDATE_STICKY_FEET', m)


def publishStickyHand(handFrame, affordanceItem=None):

    worldAffordanceId = affordance.publishWorldAffordance()

    m = lcmdrc.desired_grasp_state_t()
    m.utime = 0
    m.robot_name = 'atlas'
    m.object_name = worldAffordanceId
    m.geometry_name = 'box_0'
    m.unique_id = 3
    m.grasp_type = m.IROBOT_RIGHT
    m.power_grasp = False

    m.l_hand_pose = poseFromFrame(vtk.vtkTransform())
    m.r_hand_pose = poseFromFrame(handFrame)

    m.num_l_joints = 0
    m.l_joint_name = []
    m.l_joint_position = []

    m.num_r_joints = 8
    m.r_joint_name = [
        'right_finger[0]/joint_base_rotation',
        'right_finger[0]/joint_base',
        'right_finger[0]/joint_flex',
        'right_finger[1]/joint_base_rotation',
        'right_finger[1]/joint_base',
        'right_finger[1]/joint_flex',
        'right_finger[2]/joint_base',
        'right_finger[2]/joint_flex',
        ]

    m.r_joint_position = [
        0.0, 0.0, 0.0,
        0.0, 0.0, 0.0,
        0.0, 0.0,
        ]

    lcmUtils.publish('CANDIDATE_GRASP', m)


def cropToPlane(polyData, origin, normal, threshold):
    polyData = shallowCopy(polyData)
    normal = normal/np.linalg.norm(normal)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    dist = np.dot(points - origin, normal)
    vtkNumpy.addNumpyToVtk(polyData, dist, 'dist_to_plane')
    cropped = thresholdPoints(polyData, 'dist_to_plane', threshold)
    return cropped, polyData


def createLine(blockDimensions, p1, p2):


    sliceWidth = np.array(blockDimensions).max()/2.0 + 0.02
    sliceThreshold =  [-sliceWidth, sliceWidth]


    # require p1 to be point on left
    if p1[0] > p2[0]:
        p1, p2 = p2, p1

    _, worldPt1 = getRayFromDisplayPoint(getSegmentationView(), p1)
    _, worldPt2 = getRayFromDisplayPoint(getSegmentationView(), p2)

    cameraPt = np.array(getSegmentationView().camera().GetPosition())

    leftRay = worldPt1 - cameraPt
    rightRay = worldPt2 - cameraPt
    middleRay = (leftRay + rightRay) / 2.0


    d = DebugData()
    d.addLine(cameraPt, worldPt1)
    d.addLine(cameraPt, worldPt2)
    d.addLine(worldPt1, worldPt2)
    d.addLine(cameraPt, cameraPt + middleRay)
    updatePolyData(d.getPolyData(), 'line annotation', parent=getDebugFolder(), visible=False)

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = shallowCopy(inputObj.polyData)

    origin = cameraPt

    normal = np.cross(rightRay, leftRay)
    leftNormal = np.cross(normal, leftRay)
    rightNormal = np.cross(rightRay, normal)

    normal /= np.linalg.norm(normal)
    leftNormal /= np.linalg.norm(leftNormal)
    rightNormal /= np.linalg.norm(rightNormal)
    middleRay /= np.linalg.norm(middleRay)

    cropped, polyData = cropToPlane(polyData, origin, normal, sliceThreshold)

    updatePolyData(polyData, 'slice dist', parent=getDebugFolder(), colorByName='dist_to_plane', colorByRange=[-0.5, 0.5], visible=False)
    updatePolyData(cropped, 'slice',  parent=getDebugFolder(), colorByName='dist_to_plane', visible=False)

    cropped, _ = cropToPlane(cropped, origin, leftNormal, [-1e6, 0])
    cropped, _ = cropToPlane(cropped, origin, rightNormal, [-1e6, 0])

    updatePolyData(cropped, 'slice segment', parent=getDebugFolder(), colorByName='dist_to_plane', visible=False)

    planePoints, planeNormal = applyPlaneFit(cropped, distanceThreshold=0.005, perpendicularAxis=middleRay, angleEpsilon=math.radians(60))
    planePoints = thresholdPoints(planePoints, 'dist_to_plane', [-0.005, 0.005])
    updatePolyData(planePoints, 'board segmentation', parent=getDebugFolder(), color=getRandomColor(), visible=False)

    '''
    names = ['board A', 'board B', 'board C', 'board D', 'board E', 'board F', 'board G', 'board H', 'board I']
    for name in names:
        if not om.findObjectByName(name):
            break
    else:
        name = 'board'
    '''
    name = 'board'

    segmentBlockByTopPlane(planePoints, blockDimensions, expectedNormal=-middleRay, expectedXAxis=middleRay, edgeSign=-1, name=name)


def updateBlockAffordances(polyData=None):

    for obj in om.objects.values():
        if isinstance(obj, BlockAffordanceItem):
            if 'refit' in obj.getProperty('Name'):
                om.removeFromObjectModel(obj)

    for obj in om.objects.values():
        if isinstance(obj, BlockAffordanceItem):
            updateBlockFit(obj, polyData)


def updateBlockFit(affordanceObj, polyData=None):

    affordanceObj.updateParamsFromActorTransform()

    name = affordanceObj.getProperty('Name') + ' refit'
    origin = affordanceObj.params['origin']
    normal = affordanceObj.params['yaxis']
    edgePerpAxis = affordanceObj.params['xaxis']
    blockDimensions = [affordanceObj.params['xwidth'], affordanceObj.params['ywidth']]

    if polyData is None:
        inputObj = om.findObjectByName('pointcloud snapshot')
        polyData = shallowCopy(inputObj.polyData)

    cropThreshold = 0.1
    cropped = polyData
    cropped, _ = cropToPlane(cropped, origin, normal, [-cropThreshold, cropThreshold])
    cropped, _ = cropToPlane(cropped, origin, edgePerpAxis, [-cropThreshold, cropThreshold])

    updatePolyData(cropped, 'refit search region', parent=getDebugFolder(), visible=False)

    cropped = extractLargestCluster(cropped)

    planePoints, planeNormal = applyPlaneFit(cropped, distanceThreshold=0.005, perpendicularAxis=normal, angleEpsilon=math.radians(10))
    planePoints = thresholdPoints(planePoints, 'dist_to_plane', [-0.005, 0.005])
    updatePolyData(planePoints, 'refit board segmentation', parent=getDebugFolder(), visible=False)

    refitObj = segmentBlockByTopPlane(planePoints, blockDimensions, expectedNormal=normal, expectedXAxis=edgePerpAxis, edgeSign=-1, name=name)

    refitOrigin = np.array(refitObj.params['origin'])
    refitLength = refitObj.params['zwidth']
    refitZAxis = refitObj.params['zaxis']
    refitEndPoint1 = refitOrigin + refitZAxis*refitLength/2.0

    originalLength = affordanceObj.params['zwidth']
    correctedOrigin = refitEndPoint1 - refitZAxis*originalLength/2.0
    originDelta = correctedOrigin - refitOrigin

    refitObj.params['zwidth'] = originalLength
    refitObj.polyData.DeepCopy(affordanceObj.polyData)
    refitObj.actor.GetUserTransform().Translate(originDelta)
    refitObj.updateParamsFromActorTransform()


def startInteractiveLineDraw(blockDimensions):

    picker = LineDraw(getSegmentationView())
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = functools.partial(createLine, blockDimensions)


def startLeverValveSegmentation():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentLeverValve)


def refitValveAffordance(aff, point1, origin, normal):

    xaxis = aff.params['xaxis']
    yaxis = aff.params['yaxis']
    zaxis = aff.params['zaxis']
    origin = aff.params['origin']

    zaxis = normal
    xaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis /= np.linalg.norm(yaxis)
    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    aff.actor.GetUserTransform().SetMatrix(t.GetMatrix())
    aff.updateParamsFromActorTransform()


def segmentValve(expectedValveRadius, point1, point2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, _, wallNormal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)


    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'wall points', parent=getDebugFolder(), visible=False)


    polyData, _, _ = applyPlaneFit(polyData, expectedNormal=wallNormal, searchOrigin=point2, searchRadius=expectedValveRadius, angleEpsilon=0.2, returnOrigin=True)
    valveCluster = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    valveCluster = cropToSphere(valveCluster, point2, expectedValveRadius*2)
    valveCluster = extractLargestCluster(valveCluster,  minClusterSize=1)
    updatePolyData(valveCluster, 'valve cluster', parent=getDebugFolder(), visible=False)
    origin = np.average(vtkNumpy.getNumpyFromVtk(valveCluster, 'Points') , axis=0)

    zaxis = wallNormal
    xaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis /= np.linalg.norm(yaxis)
    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    zwidth = 0.03
    radius = expectedValveRadius


    d = DebugData()
    d.addLine(np.array([0,0,-zwidth/2.0]), np.array([0,0,zwidth/2.0]), radius=radius)

    name = 'valve affordance'
    obj = showPolyData(d.getPolyData(), name, cls=CylinderAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())
    refitWallCallbacks.append(functools.partial(refitValveAffordance, obj))

    params = dict(axis=zaxis, radius=radius, length=zwidth, origin=origin, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis,
                  xwidth=radius, ywidth=radius, zwidth=zwidth,
                  otdf_type='steering_cyl', friendly_name='valve')

    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()

    frameObj = showFrame(obj.actor.GetUserTransform(), name + ' frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())


def segmentValveByWallPlane(expectedValveRadius, point1, point2):


    centerPoint = (point1 + point2) / 2.0

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    cameraPos = np.array(getSegmentationView().camera().GetPosition())

    #bodyX = perception._multisenseItem.model.getAxis('body', [1.0, 0.0, 0.0])
    bodyX = centerPoint - cameraPos
    bodyX /= np.linalg.norm(bodyX)

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=-bodyX, searchOrigin=point1, searchRadius=0.2, returnOrigin=True)


    perpLine = np.cross(point2 - point1, normal)
    #perpLine /= np.linalg.norm(perpLine)
    #perpLine * np.linalg.norm(point2 - point1)/2.0
    point3, point4 = centerPoint + perpLine/2.0, centerPoint - perpLine/2.0

    d = DebugData()
    d.addLine(point1, point2)
    d.addLine(point3, point4)
    updatePolyData(d.getPolyData(), 'crop lines', parent=getDebugFolder(), visible=False)

    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'valve wall', parent=getDebugFolder(), visible=False)

    searchRegion = thresholdPoints(polyData, 'dist_to_plane', [0.05, 0.5])
    searchRegion = cropToLineSegment(searchRegion, point1, point2)
    searchRegion = cropToLineSegment(searchRegion, point3, point4)

    updatePolyData(searchRegion, 'valve search region', parent=getDebugFolder(), visible=False)


    searchRegion, origin, _  = applyPlaneFit(searchRegion, expectedNormal=normal, perpendicularAxis=normal, returnOrigin=True)
    searchRegion = thresholdPoints(searchRegion, 'dist_to_plane', [-0.015, 0.015])

    updatePolyData(searchRegion, 'valve search region 2', parent=getDebugFolder(), visible=False)


    largestCluster = extractLargestCluster(searchRegion, minClusterSize=1)

    updatePolyData(largestCluster, 'valve cluster', parent=getDebugFolder(), visible=False)


    #radiusLimit = [expectedValveRadius - 0.01, expectedValveRadius + 0.01] if expectedValveRadius else None
    radiusLimit = None

    polyData, circleFit = extractCircle(largestCluster, distanceThreshold=0.01, radiusLimit=radiusLimit)
    updatePolyData(polyData, 'circle fit', parent=getDebugFolder(), visible=False)


    #polyData, circleFit = extractCircle(polyData, distanceThreshold=0.01)
    #showPolyData(polyData, 'circle fit', colorByName='z')


    radius = circleFit.GetCircleRadius()
    origin = np.array(circleFit.GetCircleOrigin())
    circleNormal = np.array(circleFit.GetCircleNormal())
    circleNormal = circleNormal/np.linalg.norm(circleNormal)

    if np.dot(circleNormal, normal) < 0:
        circleNormal *= -1

    # force use of the plane normal
    circleNormal = normal
    radius = expectedValveRadius

    d = DebugData()
    d.addLine(origin - normal*radius, origin + normal*radius)
    d.addCircle(origin, circleNormal, radius)
    updatePolyData(d.getPolyData(), 'valve axes', parent=getDebugFolder(), visible=False)


    zaxis = circleNormal
    xaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis /= np.linalg.norm(yaxis)
    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    zwidth = 0.03

    d = DebugData()
    d.addLine(np.array([0,0,-zwidth/2.0]), np.array([0,0,zwidth/2.0]), radius=radius)

    name = 'valve affordance'
    obj = showPolyData(d.getPolyData(), name, cls=CylinderAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())
    refitWallCallbacks.append(functools.partial(refitValveAffordance, obj))

    params = dict(axis=zaxis, radius=radius, length=zwidth, origin=origin, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis,
                  xwidth=radius, ywidth=radius, zwidth=zwidth,
                  otdf_type='steering_cyl', friendly_name='valve')

    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()

    frameObj = showFrame(obj.actor.GetUserTransform(), name + ' frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())


def applyICP(source, target):

    icp = vtk.vtkIterativeClosestPointTransform()
    icp.SetSource(source)
    icp.SetTarget(target)
    icp.GetLandmarkTransform().SetModeToRigidBody()
    icp.Update()
    t = vtk.vtkTransform()
    t.SetMatrix(icp.GetMatrix())
    return t



def segmentLeverValve(point1, point2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)


    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'wall points', parent=getDebugFolder(), visible=False)

    radius = 0.01
    length = 0.33

    zaxis = normal
    xaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis /= np.linalg.norm(yaxis)
    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(point2)

    leverP1 = point2
    leverP2 = point2 + xaxis * length
    d = DebugData()
    d.addLine([0,0,0], [length, 0, 0], radius=radius)
    geometry = d.getPolyData()


    obj = showPolyData(geometry, 'valve lever', cls=FrameAffordanceItem, color=[0,1,0], visible=True)
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())
    frameObj = showFrame(t, 'lever frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())

    otdfType = 'lever_valve'
    params = dict(origin=np.array(t.GetPosition()), xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1, radius=radius, length=length, friendly_name=otdfType, otdf_type=otdfType)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()


def segmentWye(point1, point2):


    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)


    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'wall points', parent=getDebugFolder(), visible=False)

    wyeMesh = ioUtils.readPolyData(os.path.join(app.getDRCBase(), 'software/models/otdf/wye.obj'))

    wyeMeshPoint = np.array([0.0, 0.0, 0.005])
    wyeMeshLeftHandle = np.array([0.032292, 0.02949, 0.068485])

    xaxis = -normal
    zaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    zaxis = np.cross(xaxis, yaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Translate(-wyeMeshPoint)
    t.PostMultiply()
    t.Translate(point2)

    d = DebugData()
    d.addSphere(point2, radius=0.005)
    updatePolyData(d.getPolyData(), 'wye pick point', parent=getDebugFolder(), visible=False)

    wyeObj = showPolyData(wyeMesh, 'wye', cls=FrameAffordanceItem, color=[0,1,0], visible=True)
    wyeObj.actor.SetUserTransform(t)
    wyeObj.addToView(app.getDRCView())
    frameObj = showFrame(t, 'wye frame', parent=wyeObj, visible=False)
    frameObj.addToView(app.getDRCView())

    params = dict(origin=np.array(t.GetPosition()), xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1, friendly_name='wye', otdf_type='wye')
    wyeObj.setAffordanceParams(params)
    wyeObj.updateParamsFromActorTransform()


def segmentDoorHandle(otdfType, point1, point2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)

    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'wall points', parent=getDebugFolder(), visible=False)

    handlePoint = np.array([0.005, 0.065, 0.011])

    xaxis = -normal
    zaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    zaxis = np.cross(xaxis, yaxis)

    xwidth = 0.01
    ywidth = 0.13
    zwidth = 0.022
    cube = vtk.vtkCubeSource()
    cube.SetXLength(xwidth)
    cube.SetYLength(ywidth)
    cube.SetZLength(zwidth)
    cube.Update()
    cube = shallowCopy(cube.GetOutput())

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    #t.PreMultiply()
    #t.Translate(-handlePoint)
    t.PostMultiply()
    t.Translate(point2)

    name = 'door handle'
    obj = showPolyData(cube, name, cls=FrameAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())

    params = dict(origin=origin, xwidth=xwidth, ywidth=ywidth, zwidth=zwidth, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, friendly_name=name, otdf_type=otdfType)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()

    frameObj = showFrame(obj.actor.GetUserTransform(), name + ' frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())


def segmentTruss(point1, point2):



    edge = point2 - point1
    edgeLength = np.linalg.norm(edge)

    stanceOffset = [-0.42, 0.0, 0.0]
    stanceYaw = 0.0


    d = DebugData()
    p1 = [0.0, 0.0, 0.0]
    p2 = -np.array([0.0, -1.0, 0.0]) * edgeLength
    d.addSphere(p1, radius=0.02)
    d.addSphere(p2, radius=0.02)
    d.addLine(p1, p2)

    stanceTransform = vtk.vtkTransform()
    stanceTransform.PostMultiply()
    stanceTransform.Translate(stanceOffset)
    #stanceTransform.RotateZ(stanceYaw)

    geometry = transformPolyData(d.getPolyData(), stanceTransform.GetLinearInverse())

    yaxis = edge/edgeLength
    zaxis = [0.0, 0.0, 1.0]
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)


    xwidth = 0.1
    ywidth = edgeLength
    zwidth = 0.1

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Concatenate(stanceTransform)
    t.PostMultiply()
    t.Translate(point1)

    name = 'truss'
    otdfType = 'robot_knees'
    obj = showPolyData(geometry, name, cls=FrameAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())

    params = dict(origin=t.GetPosition(), xwidth=xwidth, ywidth=ywidth, zwidth=zwidth, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, friendly_name=name, otdf_type=otdfType)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()

    frameObj = showFrame(obj.actor.GetUserTransform(), name + ' frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())


def segmentHoseNozzle(point1):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    searchRegion = cropToSphere(polyData, point1, 0.10)
    updatePolyData(searchRegion, 'nozzle search region', parent=getDebugFolder(), visible=False)

    xaxis = [1,0,0]
    yaxis = [0,-1,0]
    zaxis = [0,0,-1]
    origin = point1

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(point1)

    nozzleRadius = 0.0266
    nozzleLength = 0.042
    nozzleTipRadius = 0.031
    nozzleTipLength = 0.024


    d = DebugData()
    d.addLine(np.array([0,0,-nozzleLength/2.0]), np.array([0,0,nozzleLength/2.0]), radius=nozzleRadius)
    d.addLine(np.array([0,0,nozzleLength/2.0]), np.array([0,0,nozzleLength/2.0 + nozzleTipLength]), radius=nozzleTipRadius)

    obj = showPolyData(d.getPolyData(), 'hose nozzle', cls=FrameAffordanceItem, color=[0,1,0], visible=True)
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())
    frameObj = showFrame(t, 'nozzle frame', parent=obj, visible=False)
    frameObj.addToView(app.getDRCView())

    params = dict(origin=origin, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1, friendly_name='firehose', otdf_type='firehose')
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()


def segmentDrillWall(point1, point2, point3):


    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData



    points = [point1, point2, point3]

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())
    expectedNormal = np.cross(point2 - point1, point3 - point1)
    expectedNormal /= np.linalg.norm(expectedNormal)
    if np.dot(expectedNormal, viewPlaneNormal) < 0:
        expectedNormal *= -1.0

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=expectedNormal, searchOrigin=(point1 + point2 + point3)/3.0, searchRadius=0.3, angleEpsilon=0.3, returnOrigin=True)

    points = [projectPointToPlane(point, origin, normal) for point in points]

    xaxis = -normal
    zaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    zaxis = np.cross(xaxis, yaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(points[0])

    d = DebugData()
    pointsInWallFrame = []
    for p in points:
        pp = np.zeros(3)
        t.GetLinearInverse().TransformPoint(p, pp)
        pointsInWallFrame.append(pp)
        d.addSphere(pp, radius=0.02)

    for a, b in zip(pointsInWallFrame, pointsInWallFrame[1:] + [pointsInWallFrame[0]]):
        d.addLine(a, b, radius=0.015)

    aff = showPolyData(d.getPolyData(), 'drill targets', cls=FrameAffordanceItem, color=[0,1,0], visible=True)
    aff.actor.SetUserTransform(t)
    showFrame(t, 'wall frame', parent=aff, visible=False)
    refitWallCallbacks.append(functools.partial(refitDrillWall, aff))

    params = dict(origin=points[0], xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1,
                  p1y=pointsInWallFrame[0][1], p1z=pointsInWallFrame[0][2],
                  p2y=pointsInWallFrame[1][1], p2z=pointsInWallFrame[1][2],
                  p3y=pointsInWallFrame[2][1], p3z=pointsInWallFrame[2][2],
                  friendly_name='drill_wall', otdf_type='drill_wall')

    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())



refitWallCallbacks = []

def refitWall(point1):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)

    wallPoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(wallPoints, 'wall points', parent=getDebugFolder(), visible=False)

    for func in refitWallCallbacks:
        func(point1, origin, normal)


def refitDrillWall(aff, point1, origin, normal):

    t = aff.actor.GetUserTransform()

    targetOrigin = np.array(t.GetPosition())

    projectedOrigin = projectPointToPlane(targetOrigin, origin, normal)
    projectedOrigin[2] = targetOrigin[2]

    xaxis = -normal
    zaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    zaxis = np.cross(xaxis, yaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(projectedOrigin)
    aff.actor.GetUserTransform().SetMatrix(t.GetMatrix())


def getGroundHeightFromFeet():
    rfoot = getLinkFrame('r_foot')
    return np.array(rfoot.GetPosition())[2] -  0.0745342


def getTranslationRelativeToFoot(t):

    rfoot = getLinkFrame('r_foot')


def segmentDrillWallConstrained(rightAngleLocation, point1, point2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())
    expectedNormal = np.cross(point2 - point1, [0.0, 0.0, 1.0])
    expectedNormal /= np.linalg.norm(expectedNormal)
    if np.dot(expectedNormal, viewPlaneNormal) < 0:
        expectedNormal *= -1.0

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=expectedNormal, searchOrigin=point1, searchRadius=0.3, angleEpsilon=0.3, returnOrigin=True)

    triangleOrigin = projectPointToPlane(point2, origin, normal)

    xaxis = -normal
    zaxis = [0, 0, 1]
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    zaxis = np.cross(xaxis, yaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(triangleOrigin)


    edgeRight = np.array([0.0, -1.0, 0.0]) * (24 * .0254)
    edgeUp = np.array([0.0, 0.0, 1.0]) * (12 * .0254)


    pointsInWallFrame = np.zeros((3,3))

    if rightAngleLocation == DRILL_TRIANGLE_BOTTOM_LEFT:
        pointsInWallFrame[1] = edgeUp
        pointsInWallFrame[2] =  edgeRight

    elif rightAngleLocation == DRILL_TRIANGLE_BOTTOM_RIGHT:
        pointsInWallFrame[1] = edgeRight + edgeUp
        pointsInWallFrame[2] = edgeRight

    elif rightAngleLocation == DRILL_TRIANGLE_TOP_LEFT:
        pointsInWallFrame[1] = edgeRight
        pointsInWallFrame[2] = -edgeUp

    elif rightAngleLocation == DRILL_TRIANGLE_TOP_RIGHT:
        pointsInWallFrame[1] = edgeRight
        pointsInWallFrame[2] = edgeRight - edgeUp
    else:
        raise Exception('unexpected value for right angle location: ', + rightAngleLocation)

    center = pointsInWallFrame.sum(axis=0)/3.0
    shrinkFactor = 0.90
    shrinkPoints = (pointsInWallFrame - center) * shrinkFactor + center

    d = DebugData()
    for p in pointsInWallFrame:
        d.addSphere(p, radius=0.02)

    for a, b in zip(pointsInWallFrame, np.vstack((pointsInWallFrame[1:], pointsInWallFrame[0]))):
        d.addLine(a, b, radius=0.01)

    for a, b in zip(shrinkPoints, np.vstack((shrinkPoints[1:], shrinkPoints[0]))):
        d.addLine(a, b, radius=0.0025)

    aff = showPolyData(d.getPolyData(), 'drill targets', cls=FrameAffordanceItem, color=[0,1,0], visible=True)
    aff.actor.SetUserTransform(t)
    refitWallCallbacks.append(functools.partial(refitDrillWall, aff))
    frameObj = showFrame(t, 'wall frame', parent=aff, visible=False)
    frameObj.addToView(app.getDRCView())

    params = dict(origin=triangleOrigin, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1,
                  p1y=shrinkPoints[0][1], p1z=shrinkPoints[0][2],
                  p2y=shrinkPoints[1][1], p2z=shrinkPoints[1][2],
                  p3y=shrinkPoints[2][1], p3z=shrinkPoints[2][2],
                  friendly_name='drill_wall', otdf_type='drill_wall')

    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())

    '''
    rfoot = getLinkFrame('r_foot')
    tt = getTransformFromAxes(xaxis, yaxis, zaxis)
    tt.PostMultiply()
    tt.Translate(rfoot.GetPosition())
    showFrame(tt, 'rfoot with wall orientation')
    aff.footToAffTransform = computeAToB(tt, t)

    footToAff = list(aff.footToAffTransform.GetPosition())
    tt.TransformVector(footToAff, footToAff)

    d = DebugData()
    d.addSphere(tt.GetPosition(), radius=0.02)
    d.addLine(tt.GetPosition(), np.array(tt.GetPosition()) + np.array(footToAff))
    showPolyData(d.getPolyData(), 'rfoot debug')
    '''


def getDrillAffordanceParams(origin, xaxis, yaxis, zaxis):

    params = dict(origin=origin, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, xwidth=0.1, ywidth=0.1, zwidth=0.1,
                  button_x=0.035,
                  button_y=0.007,
                  button_z=-0.06,
                  guard_x=0.0,
                  guard_y=-0.01,
                  guard_z=0.15,
                  guard_nx=0.0,
                  guard_ny=0.0,
                  guard_nz=1.0,
                  button_nx=1.0,
                  button_ny=0.0,
                  button_nz=0.0,
                  friendly_name='dewalt_button', otdf_type='dewalt_button')

    return params


def getDrillMesh():

    button = np.array([0.035, 0.007, -0.06])

    drillMesh = ioUtils.readPolyData(os.path.join(app.getDRCBase(), 'software/models/otdf/dewalt_button.obj'))
    d = DebugData()
    d.addPolyData(drillMesh)
    d.addSphere(button, radius=0.005, color=[0,1,0])
    return d.getPolyData()


def getDrillBarrelMesh():
    return ioUtils.readPolyData(os.path.join(app.getDRCBase(), 'software/models/otdf/dewalt.ply'), computeNormals=True)


def segmentDrill(point1, point2, point3):


    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=viewPlaneNormal, searchOrigin=point1, searchRadius=0.2, angleEpsilon=0.7, returnOrigin=True)


    tablePoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(tablePoints, 'table plane points', parent=getDebugFolder(), visible=False)


    searchRegion = thresholdPoints(polyData, 'dist_to_plane', [0.03, 0.4])
    searchRegion = cropToSphere(searchRegion, point2, 0.30)
    drillPoints = extractLargestCluster(searchRegion)

    drillToTopPoint = np.array([-0.002904, -0.010029, 0.153182])

    zaxis = normal
    yaxis = point3 - point2
    yaxis /= np.linalg.norm(yaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(zaxis, xaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Translate(-drillToTopPoint)
    t.PostMultiply()
    t.Translate(point2)

    drillMesh = getDrillMesh()

    aff = showPolyData(drillMesh, 'drill', cls=FrameAffordanceItem, visible=True)
    aff.actor.SetUserTransform(t)
    showFrame(t, 'drill frame', parent=aff, visible=False).addToView(app.getDRCView())

    params = getDrillAffordanceParams(origin, xaxis, yaxis, zaxis)
    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())


def segmentDrillAuto(point1):


    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    expectedNormal = np.array([0.0, 0.0, 1.0])

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=expectedNormal, perpendicularAxis=expectedNormal, searchOrigin=point1, searchRadius=0.4, angleEpsilon=0.2, returnOrigin=True)


    tablePoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(tablePoints, 'table plane points', parent=getDebugFolder(), visible=False)

    tablePoints = labelDistanceToPoint(tablePoints, point1)
    tablePointsClusters = extractClusters(tablePoints)
    tablePointsClusters.sort(key=lambda x: vtkNumpy.getNumpyFromVtk(x, 'distance_to_point').min())

    tablePoints = tablePointsClusters[0]
    updatePolyData(tablePoints, 'table points', parent=getDebugFolder(), visible=False)

    searchRegion = thresholdPoints(polyData, 'dist_to_plane', [0.03, 0.4])
    searchRegion = cropToSphere(searchRegion, point1, 0.30)
    drillPoints = extractLargestCluster(searchRegion, minClusterSize=1)


    # determine drill orientation (rotation about z axis)

    centroids = computeCentroids(drillPoints, axis=normal)

    centroidsPolyData = vtkNumpy.getVtkPolyDataFromNumpyPoints(centroids)
    d = DebugData()
    updatePolyData(centroidsPolyData, 'cluster centroids', parent=getDebugFolder(), visible=False)

    drillToTopPoint = np.array([-0.002904, -0.010029, 0.153182])

    zaxis = normal
    yaxis = centroids[0] - centroids[-1]
    yaxis /= np.linalg.norm(yaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(zaxis, xaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Translate(-drillToTopPoint)
    t.PostMultiply()
    t.Translate(centroids[-1])

    drillMesh = getDrillMesh()

    aff = showPolyData(drillMesh, 'drill', cls=FrameAffordanceItem, visible=True)
    aff.actor.SetUserTransform(t)
    showFrame(t, 'drill frame', parent=aff, visible=False).addToView(app.getDRCView())

    params = getDrillAffordanceParams(origin, xaxis, yaxis, zaxis)
    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())



def findAndFitDrillBarrel(polyData=None, robotFrame=None):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = polyData or inputObj.polyData

    groundPoints, scenePoints =  removeGround(polyData, groundThickness=0.02, sceneHeightFromGround=0.50)

    scenePoints = thresholdPoints(scenePoints, 'dist_to_plane', [0.5, 1.7])

    if not scenePoints.GetNumberOfPoints():
        return

    normalEstimationSearchRadius = 0.10

    f = pcl.vtkPCLNormalEstimation()
    f.SetSearchRadius(normalEstimationSearchRadius)
    f.SetInput(scenePoints)
    f.Update()
    scenePoints = shallowCopy(f.GetOutput())

    normals = vtkNumpy.getNumpyFromVtk(scenePoints, 'normals')
    normalsDotUp = np.abs(np.dot(normals, [0,0,1]))

    vtkNumpy.addNumpyToVtk(scenePoints, normalsDotUp, 'normals_dot_up')

    surfaces = thresholdPoints(scenePoints, 'normals_dot_up', [0.95, 1.0])


    updatePolyData(groundPoints, 'ground points', parent=getDebugFolder(), visible=False)
    updatePolyData(scenePoints, 'scene points', parent=getDebugFolder(), colorByName='normals_dot_up', visible=False)
    updatePolyData(surfaces, 'surfaces', parent=getDebugFolder(), visible=False)

    clusters = extractClusters(surfaces, clusterTolerance=0.15, minClusterSize=50)

    fitResults = []

    robotOrigin = np.array(robotFrame.GetPosition())
    robotForward = np.array([1.0, 0.0, 0.0])
    robotFrame.TransformVector(robotForward, robotForward)

    #print 'robot origin:', robotOrigin
    #print 'robot forward:', robotForward

    for clusterId, cluster in enumerate(clusters):
        clusterObj = updatePolyData(cluster, 'surface cluster %d' % clusterId, color=[1,1,0], parent=getDebugFolder(), visible=False)

        origin, edges = getOrientedBoundingBox(cluster)
        edgeLengths = [np.linalg.norm(edge) for edge in edges[:2]]

        skipCluster = False
        for edgeLength in edgeLengths:
            #print 'cluster %d edge length: %f' % (clusterId, edgeLength)
            if edgeLength < 0.35 or edgeLength > 0.75:
                skipCluster = True

        if skipCluster:
            continue

        clusterObj.setSolidColor([0, 0, 1])
        centroid = np.average(vtkNumpy.getNumpyFromVtk(cluster, 'Points'), axis=0)

        try:
            drillFrame = segmentDrillBarrelFrame(centroid, polyData=scenePoints, forwardDirection=robotForward)
            if drillFrame is not None:
                fitResults.append((clusterObj, drillFrame))
        except:
            print traceback.format_exc()
            print 'fit drill failed for cluster:', clusterId

    if not fitResults:
        return


    angleToFitResults = []

    for fitResult in fitResults:
        cluster, drillFrame = fitResult
        drillOrigin = np.array(drillFrame.GetPosition())
        angleToDrill = np.abs(computeSignedAngleBetweenVectors(robotForward, drillOrigin - robotOrigin, [0,0,1]))
        angleToFitResults.append((angleToDrill, cluster, drillFrame))
        #print 'angle to candidate drill:', angleToDrill

    angleToFitResults.sort(key=lambda x: x[0])

    #print 'using drill at angle:', angleToFitResults[0][0]

    drillMesh = getDrillBarrelMesh()

    for i, fitResult in enumerate(angleToFitResults):

        angleToDrill, cluster, drillFrame = fitResult

        if i == 0:

            drill = om.findObjectByName('drill')
            drill = updatePolyData(drillMesh, 'drill', color=[0, 1, 0], visible=True)
            drillFrame = updateFrame(drillFrame, 'drill frame', parent=drill, visible=False)
            drill.actor.SetUserTransform(drillFrame.transform)

            drill.setSolidColor([0, 1, 0])
            cluster.setProperty('Visible', True)

        else:

            drill = showPolyData(drillMesh, 'drill candidate', color=[1,0,0], visible=False, parent=getDebugFolder())
            drill.actor.SetUserTransform(drillFrame)
            om.addToObjectModel(drill, parentObj=getDebugFolder())


def computeSignedAngleBetweenVectors(v1, v2, perpendicularVector):
    '''
    Computes the signed angle between two vectors in 3d, given a perpendicular vector
    to determine sign.  Result returned is radians.
    '''
    v1 = np.array(v1)
    v2 = np.array(v2)
    perpendicularVector = np.array(perpendicularVector)
    v1 /= np.linalg.norm(v1)
    v2 /= np.linalg.norm(v2)
    perpendicularVector /= np.linalg.norm(perpendicularVector)
    return math.atan2(np.dot(perpendicularVector, np.cross(v1, v2)), np.dot(v1, v2))


def segmentDrillBarrelFrame(point1, polyData, forwardDirection):

    tableClusterSearchRadius = 0.4
    drillClusterSearchRadius = 0.5 #0.3


    expectedNormal = np.array([0.0, 0.0, 1.0])

    if not polyData.GetNumberOfPoints():
        return

    polyData, origin, normal = applyPlaneFit(polyData, expectedNormal=expectedNormal,
        perpendicularAxis=expectedNormal, searchOrigin=point1,
        searchRadius=tableClusterSearchRadius, angleEpsilon=0.2, returnOrigin=True)


    if not polyData.GetNumberOfPoints():
        return

    tablePoints = thresholdPoints(polyData, 'dist_to_plane', [-0.01, 0.01])
    updatePolyData(tablePoints, 'table plane points', parent=getDebugFolder(), visible=False)

    tablePoints = labelDistanceToPoint(tablePoints, point1)
    tablePointsClusters = extractClusters(tablePoints)
    tablePointsClusters.sort(key=lambda x: vtkNumpy.getNumpyFromVtk(x, 'distance_to_point').min())

    if not tablePointsClusters:
        return

    tablePoints = tablePointsClusters[0]
    updatePolyData(tablePoints, 'table points', parent=getDebugFolder(), visible=False)

    searchRegion = thresholdPoints(polyData, 'dist_to_plane', [0.02, 0.3])
    if not searchRegion.GetNumberOfPoints():
        return

    searchRegion = cropToSphere(searchRegion, point1, drillClusterSearchRadius)
    #drillPoints = extractLargestCluster(searchRegion, minClusterSize=1)
    drillPoints = searchRegion

    if not drillPoints.GetNumberOfPoints():
        return

    updatePolyData(drillPoints, 'drill cluster', parent=getDebugFolder(), visible=False)
    drillBarrelPoints = thresholdPoints(drillPoints, 'dist_to_plane', [0.177, 0.30])

    if not drillBarrelPoints.GetNumberOfPoints():
        return


    # fit line to drill barrel points
    linePoint, lineDirection, _ = applyLineFit(drillBarrelPoints, distanceThreshold=0.5)

    if np.dot(lineDirection, forwardDirection) < 0:
        lineDirection = -lineDirection

    updatePolyData(drillBarrelPoints, 'drill barrel points', parent=getDebugFolder(), visible=False)


    pts = vtkNumpy.getNumpyFromVtk(drillBarrelPoints, 'Points')

    dists = np.dot(pts-linePoint, lineDirection)

    p1 = linePoint + lineDirection*np.min(dists)
    p2 = linePoint + lineDirection*np.max(dists)

    p1 = projectPointToPlane(p1, origin, normal)
    p2 = projectPointToPlane(p2, origin, normal)


    d = DebugData()
    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)
    updatePolyData(d.getPolyData(), 'drill debug points', color=[0,1,0], parent=getDebugFolder(), visible=False)


    drillToBasePoint = np.array([-0.07,  0.0  , -0.12])

    zaxis = normal
    xaxis = lineDirection
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Translate(-drillToBasePoint)
    t.PostMultiply()
    t.Translate(p1)

    return t


def segmentDrillBarrel(point1):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    forwardDirection = -np.array(getCurrentView().camera().GetViewPlaneNormal())

    t = segmentDrillBarrel(point1, polyData, forwardDirection)
    assert t is not None

    drillMesh = getDrillBarrelMesh()

    aff = showPolyData(drillMesh, 'drill', visible=True)
    aff.addToView(app.getDRCView())

    aff.actor.SetUserTransform(t)
    drillFrame = showFrame(t, 'drill frame', parent=aff, visible=False)
    drillFrame.addToView(app.getDRCView())
    return aff, drillFrame



def segmentDrillInHand(p1, p2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = inputObj.polyData

    distanceToLineThreshold = 0.05

    polyData = labelDistanceToLine(polyData, p1, p2)
    polyData = thresholdPoints(polyData, 'distance_to_line', [0.0, distanceToLineThreshold])

    lineSegment = p2 - p1
    lineLength = np.linalg.norm(lineSegment)

    cropped, polyData = cropToPlane(polyData, p1, lineSegment/lineLength, [-0.03, lineLength + 0.03])

    updatePolyData(cropped, 'drill cluster', parent=getDebugFolder(), visible=False)


    drillPoints = cropped
    normal = lineSegment/lineLength

    centroids = computeCentroids(drillPoints, axis=normal)

    centroidsPolyData = vtkNumpy.getVtkPolyDataFromNumpyPoints(centroids)
    d = DebugData()
    updatePolyData(centroidsPolyData, 'cluster centroids', parent=getDebugFolder(), visible=False)

    drillToTopPoint = np.array([-0.002904, -0.010029, 0.153182])

    zaxis = normal
    yaxis = centroids[0] - centroids[-1]
    yaxis /= np.linalg.norm(yaxis)
    xaxis = np.cross(yaxis, zaxis)
    xaxis /= np.linalg.norm(xaxis)
    yaxis = np.cross(zaxis, xaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PreMultiply()
    t.Translate(-drillToTopPoint)
    t.PostMultiply()
    t.Translate(p2)

    drillMesh = getDrillMesh()

    aff = showPolyData(drillMesh, 'drill', cls=FrameAffordanceItem, visible=True)
    aff.actor.SetUserTransform(t)
    showFrame(t, 'drill frame', parent=aff, visible=False).addToView(app.getDRCView())

    params = getDrillAffordanceParams(np.array(t.GetPosition()), xaxis, yaxis, zaxis)
    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())



def addDrillAffordance():

    drillMesh = getDrillMesh()

    aff = showPolyData(drillMesh, 'drill', cls=FrameAffordanceItem, visible=True)
    t = vtk.vtkTransform()
    t.PostMultiply()
    aff.actor.SetUserTransform(t)
    showFrame(t, 'drill frame', parent=aff, visible=False).addToView(app.getDRCView())

    params = getDrillAffordanceParams(np.array(t.GetPosition()), [1,0,0], [0,1,0], [0,0,1])
    aff.setAffordanceParams(params)
    aff.updateParamsFromActorTransform()
    aff.addToView(app.getDRCView())
    return aff


def getLinkFrame(linkName):
    robotStateModel = om.findObjectByName('model publisher')
    robotStateModel = robotStateModel or getVisibleRobotModel()
    assert robotStateModel
    t = vtk.vtkTransform()
    robotStateModel.model.getLinkToWorld(linkName, t)
    return t


def getDrillInHandOffset(zRotation=0.0, zTranslation=0.0, flip=False):

    drillOffset = vtk.vtkTransform()
    drillOffset.PostMultiply()
    if flip:
        drillOffset.RotateY(180)
    drillOffset.RotateZ(zRotation)
    drillOffset.Translate(0, 0.09, zTranslation - 0.015)
    return drillOffset


def moveDrillToHand(drillOffset, hand='right'):
    drill = om.findObjectByName('drill')
    if not drill:
        drill = addDrillAffordance()

    assert hand in ('right', 'left')
    drillTransform = drill.actor.GetUserTransform()
    rightBaseLink = getLinkFrame('%s_base_link' % hand)
    drillTransform.PostMultiply()
    drillTransform.Identity()
    drillTransform.Concatenate(drillOffset)
    drillTransform.Concatenate(rightBaseLink)
    drill._renderAllViews()

class PointPicker(TimerCallback):

    def __init__(self, numberOfPoints=3):
        TimerCallback.__init__(self)
        self.targetFps = 30
        self.enabled = False
        self.numberOfPoints = numberOfPoints
        self.annotationObj = None
        self.drawLines = True
        self.clear()

    def clear(self):
        self.points = [None for i in xrange(self.numberOfPoints)]
        self.hoverPos = None
        self.annotationFunc = None
        self.lastMovePos = [0, 0]

    def onMouseMove(self, displayPoint, modifiers=None):
        self.lastMovePos = displayPoint

    def onMousePress(self, displayPoint, modifiers=None):

        #print 'mouse press:', modifiers
        #if not modifiers:
        #    return

        for i in xrange(self.numberOfPoints):
            if self.points[i] is None:
                self.points[i] = self.hoverPos
                break

        if self.points[-1] is not None:
            self.finish()

    def finish(self):

        self.enabled = False
        om.removeFromObjectModel(self.annotationObj)

        points = [p.copy() for p in self.points]
        if self.annotationFunc is not None:
            self.annotationFunc(*points)

        removeViewPicker(self)

    def handleRelease(self, displayPoint):
        pass

    def draw(self):

        d = DebugData()

        points = [p if p is not None else self.hoverPos for p in self.points]

        # draw points
        for p in points:
            if p is not None:
                d.addSphere(p, radius=0.01)

        if self.drawLines:
            # draw lines
            for a, b in zip(points, points[1:]):
                if b is not None:
                    d.addLine(a, b)

            # connect end points
            if points[-1] is not None:
                d.addLine(points[0], points[-1])


        self.annotationObj = updatePolyData(d.getPolyData(), 'annotation', parent=getDebugFolder())
        self.annotationObj.setProperty('Color', QtGui.QColor(0, 255, 0))


    def tick(self):

        if not self.enabled:
            return

        self.hoverPos = pickPoint(self.lastMovePos, obj='pointcloud snapshot')
        self.draw()


class LineDraw(TimerCallback):

    def __init__(self, view):
        TimerCallback.__init__(self)
        self.targetFps = 30
        self.enabled = False
        self.view = view
        self.renderer = view.renderer()
        self.line = vtk.vtkLeaderActor2D()
        self.line.SetArrowPlacementToNone()
        self.line.GetPositionCoordinate().SetCoordinateSystemToViewport()
        self.line.GetPosition2Coordinate().SetCoordinateSystemToViewport()
        self.line.GetProperty().SetLineWidth(2)
        self.line.SetPosition(0,0)
        self.line.SetPosition2(0,0)
        self.clear()

    def clear(self):
        self.p1 = None
        self.p2 = None
        self.annotationFunc = None
        self.lastMovePos = [0, 0]
        self.renderer.RemoveActor2D(self.line)

    def onMouseMove(self, displayPoint, modifiers=None):
        self.lastMovePos = displayPoint

    def onMousePress(self, displayPoint, modifiers=None):

        if self.p1 is None:
            self.p1 = list(self.lastMovePos)
            if self.p1 is not None:
                self.renderer.AddActor2D(self.line)
        else:
            self.p2 = self.lastMovePos
            self.finish()

    def finish(self):

        self.enabled = False
        self.renderer.RemoveActor2D(self.line)
        if self.annotationFunc is not None:
            self.annotationFunc(self.p1, self.p2)


    def handleRelease(self, displayPoint):
        pass

    def tick(self):

        if not self.enabled:
            return

        if self.p1:
            self.line.SetPosition(self.p1)
            self.line.SetPosition2(self.lastMovePos)
            self.view.render()

viewPickers = []

def addViewPicker(picker):
    global viewPickers
    viewPickers.append(picker)

def removeViewPicker(picker):
    global viewPickers
    viewPickers.remove(picker)


def distanceToLine(x0, x1, x2):
    numerator = np.sqrt(np.sum(np.cross((x0 - x1), (x0-x2))**2))
    denom = np.linalg.norm(x2-x1)
    return numerator / denom


def labelDistanceToLine(polyData, linePoint1, linePoint2, resultArrayName='distance_to_line'):

    x0 = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    x1 = linePoint1
    x2 = linePoint2

    numerator = np.sqrt(np.sum(np.cross((x0 - x1), (x0-x2))**2, axis=1))
    denom = np.linalg.norm(x2-x1)

    dists = numerator / denom

    polyData = shallowCopy(polyData)
    vtkNumpy.addNumpyToVtk(polyData, dists, resultArrayName)
    return polyData


def labelDistanceToPoint(polyData, point, resultArrayName='distance_to_point'):
    assert polyData.GetNumberOfPoints()
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    points = points - point
    dists = np.sqrt(np.sum(points**2, axis=1))
    polyData = shallowCopy(polyData)
    vtkNumpy.addNumpyToVtk(polyData, dists, resultArrayName)
    return polyData


def getRayFromDisplayPoint(view, displayPoint):

    worldPt1 = [0,0,0,0]
    worldPt2 = [0,0,0,0]
    renderer = view.renderer()

    vtk.vtkInteractorObserver.ComputeDisplayToWorld(renderer, displayPoint[0], displayPoint[1], 0, worldPt1)
    vtk.vtkInteractorObserver.ComputeDisplayToWorld(renderer, displayPoint[0], displayPoint[1], 1, worldPt2)

    worldPt1 = np.array(worldPt1[:3])
    worldPt2 = np.array(worldPt2[:3])
    return worldPt1, worldPt2


def getPlaneEquationFromPolyData(polyData, expectedNormal):

    _, origin, normal  = applyPlaneFit(polyData, expectedNormal=expectedNormal, returnOrigin=True)
    return origin, normal, np.hstack((normal, [np.dot(origin, normal)]))




def computeEdge(polyData, edgeAxis, perpAxis, binWidth=0.03):

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, edgeAxis, resultArrayName='dist_along_edge')
    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, perpAxis, resultArrayName='dist_perp_to_edge')


    polyData, bins = binByScalar(polyData, 'dist_along_edge', binWidth)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    binLabels = vtkNumpy.getNumpyFromVtk(polyData, 'bin_labels')
    distToEdge = vtkNumpy.getNumpyFromVtk(polyData, 'dist_perp_to_edge')

    numberOfBins = len(bins) - 1
    edgePoints = []
    for i in xrange(numberOfBins):
        binPoints = points[binLabels == i]
        binDists = distToEdge[binLabels == i]
        if len(binDists):
            edgePoints.append(binPoints[binDists.argmax()])

    return np.array(edgePoints)


def computeCentroids(polyData, axis, binWidth=0.025):

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, axis, resultArrayName='dist_along_axis')

    polyData, bins = binByScalar(polyData, 'dist_along_axis', binWidth)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    binLabels = vtkNumpy.getNumpyFromVtk(polyData, 'bin_labels')

    numberOfBins = len(bins) - 1
    centroids = []
    for i in xrange(numberOfBins):
        binPoints = points[binLabels == i]

        if len(binPoints):
            centroids.append(np.average(binPoints, axis=0))

    return np.array(centroids)


def computePointCountsAlongAxis(polyData, axis, binWidth=0.025):

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, axis, resultArrayName='dist_along_axis')

    polyData, bins = binByScalar(polyData, 'dist_along_axis', binWidth)
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')
    binLabels = vtkNumpy.getNumpyFromVtk(polyData, 'bin_labels')

    numberOfBins = len(bins) - 1
    binCount = []
    for i in xrange(numberOfBins):
        binPoints = points[binLabels == i]
        binCount.append(len(binPoints))

    return np.array(binCount)




def binByScalar(lidarData, scalarArrayName, binWidth, binLabelsArrayName='bin_labels'):
    '''
    Gets the array with name scalarArrayName from lidarData.
    Computes bins by dividing the scalar array into bins of size binWidth.
    Adds a new label array to the lidar points identifying which bin the point belongs to,
    where the first bin is labeled with 0.
    Returns the new, labeled lidar data and the bins.
    The bins are an array where each value represents a bin edge.
    '''

    scalars = vtkNumpy.getNumpyFromVtk(lidarData, scalarArrayName)
    bins = np.arange(scalars.min(), scalars.max()+binWidth, binWidth)
    binLabels = np.digitize(scalars, bins) - 1
    assert(len(binLabels) == len(scalars))
    newData = shallowCopy(lidarData)
    vtkNumpy.addNumpyToVtk(newData, binLabels, binLabelsArrayName)
    return newData, bins


def showObbs(polyData):

    labelsArrayName = 'cluster_labels'
    assert polyData.GetPointData().GetArray(labelsArrayName)

    f = pcl.vtkAnnotateOBBs()
    f.SetInputArrayToProcess(0,0,0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, labelsArrayName)
    f.SetInput(polyData)
    f.Update()
    showPolyData(f.GetOutput(), 'bboxes')


def getOrientedBoundingBox(polyData):

    nPoints = polyData.GetNumberOfPoints()
    assert nPoints
    polyData = shallowCopy(polyData)

    labelsArrayName = 'bbox_labels'
    labels = np.ones(nPoints)
    vtkNumpy.addNumpyToVtk(polyData, labels, labelsArrayName)

    f = pcl.vtkAnnotateOBBs()
    f.SetInputArrayToProcess(0,0,0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, labelsArrayName)
    f.SetInput(polyData)
    f.Update()

    assert f.GetNumberOfBoundingBoxes() == 1

    origin = np.zeros(3)
    edges = [np.zeros(3) for i in xrange(3)]

    f.GetBoundingBoxOrigin(0, origin)
    for i in xrange(3):
        f.GetBoundingBoxEdge(0, i, edges[i])

    return origin, edges


def segmentBlockByAnnotation(blockDimensions, p1, p2, p3):

    segmentationObj = om.findObjectByName('pointcloud snapshot')
    segmentationObj.mapper.ScalarVisibilityOff()
    segmentationObj.setProperty('Point Size', 2)
    segmentationObj.setProperty('Alpha', 0.8)

    # constraint z to lie in plane
    #p1[2] = p2[2] = p3[2] = max(p1[2], p2[2], p3[2])

    zedge = p2 - p1
    zaxis = zedge / np.linalg.norm(zedge)

    #xwidth = distanceToLine(p3, p1, p2)

    # expected dimensions
    xwidth, ywidth = blockDimensions

    zwidth = np.linalg.norm(zedge)

    yaxis = np.cross(p2 - p1, p3 - p1)
    yaxis = yaxis / np.linalg.norm(yaxis)

    xaxis = np.cross(yaxis, zaxis)

    # reorient axes
    viewPlaneNormal = getSegmentationView().camera().GetViewPlaneNormal()
    if np.dot(yaxis, viewPlaneNormal) < 0:
        yaxis *= -1

    if np.dot(xaxis, p3 - p1) < 0:
        xaxis *= -1

    # make right handed
    zaxis = np.cross(xaxis, yaxis)

    origin = ((p1 + p2) / 2.0) + xaxis*xwidth/2.0 + yaxis*ywidth/2.0

    d = DebugData()
    d.addSphere(origin, radius=0.01)
    d.addLine(origin - xaxis*xwidth/2.0, origin + xaxis*xwidth/2.0)
    d.addLine(origin - yaxis*ywidth/2.0, origin + yaxis*ywidth/2.0)
    d.addLine(origin - zaxis*zwidth/2.0, origin + zaxis*zwidth/2.0)
    obj = updatePolyData(d.getPolyData(), 'block axes')
    obj.setProperty('Color', QtGui.QColor(255, 255, 0))
    obj.setProperty('Visible', False)
    om.findObjectByName('annotation').setProperty('Visible', False)

    cube = vtk.vtkCubeSource()
    cube.SetXLength(xwidth)
    cube.SetYLength(ywidth)
    cube.SetZLength(zwidth)
    cube.Update()
    cube = shallowCopy(cube.GetOutput())

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    obj = updatePolyData(cube, 'block affordance', cls=BlockAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)

    obj.addToView(app.getDRCView())

    params = dict(origin=origin, xwidth=xwidth, ywidth=ywidth, zwidth=zwidth, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()


def projectPointToPlane(point, origin, normal):
    projectedPoint = np.zeros(3)
    vtk.vtkPlane.ProjectPoint(point, origin, normal, projectedPoint)
    return projectedPoint




####
# debrs task ground frame

def getBoardCorners(params):
    axes = [np.array(params[axis]) for axis in ['xaxis', 'yaxis', 'zaxis']]
    widths = [np.array(params[axis])/2.0 for axis in ['xwidth', 'ywidth', 'zwidth']]
    edges = [axes[i] * widths[i] for i in xrange(3)]
    origin = np.array(params['origin'])
    return [
            origin + edges[0] + edges[1] + edges[2],
            origin - edges[0] + edges[1] + edges[2],
            origin - edges[0] - edges[1] + edges[2],
            origin + edges[0] - edges[1] + edges[2],
            origin + edges[0] + edges[1] - edges[2],
            origin - edges[0] + edges[1] - edges[2],
            origin - edges[0] - edges[1] - edges[2],
            origin + edges[0] - edges[1] - edges[2],
           ]

def getPointDistances(target, points):
    return np.array([np.linalg.norm(target - p) for p in points])


def computeClosestCorner(aff, referenceFrame):
    corners = getBoardCorners(aff.params)
    dists = getPointDistances(np.array(referenceFrame.GetPosition()), corners)
    return corners[dists.argmin()]


def computeGroundFrame(aff, referenceFrame):

    refAxis = [0.0, -1.0, 0.0]
    referenceFrame.TransformVector(refAxis, refAxis)

    refAxis = np.array(refAxis)

    axes = [np.array(aff.params[axis]) for axis in ['xaxis', 'yaxis', 'zaxis']]
    axisProjections = np.array([np.abs(np.dot(axis, refAxis)) for axis in axes])
    boardAxis = axes[axisProjections.argmax()]
    if np.dot(boardAxis, refAxis) < 0:
        boardAxis = -boardAxis

    xaxis = boardAxis
    zaxis = np.array([0.0, 0.0, 1.0])
    yaxis = np.cross(zaxis, xaxis)
    yaxis /= np.linalg.norm(yaxis)
    xaxis = np.cross(yaxis, zaxis)
    closestCorner = computeClosestCorner(aff, referenceFrame)
    groundFrame = getTransformFromAxes(xaxis, yaxis, zaxis)
    groundFrame.PostMultiply()
    groundFrame.Translate(closestCorner[0], closestCorner[1], 0.0)
    return groundFrame


def computeCornerFrame(aff, referenceFrame):

    refAxis = [0.0, -1.0, 0.0]
    referenceFrame.TransformVector(refAxis, refAxis)

    refAxis = np.array(refAxis)

    axes = [np.array(aff.params[axis]) for axis in ['xaxis', 'yaxis', 'zaxis']]
    edgeLengths = [edgeLength for edgeLength in ['xwidth', 'ywidth', 'zwidth']]

    axisProjections = np.array([np.abs(np.dot(axis, refAxis)) for axis in axes])
    boardAxis = axes[axisProjections.argmax()]
    if np.dot(boardAxis, refAxis) < 0:
        boardAxis = -boardAxis

    longAxis = axes[np.argmax(edgeLengths)]

    xaxis = boardAxis
    yaxis = axes[2]
    zaxis = np.cross(xaxis, yaxis)

    closestCorner = computeClosestCorner(aff, referenceFrame)
    cornerFrame = getTransformFromAxes(xaxis, yaxis, zaxis)
    cornerFrame.PostMultiply()
    cornerFrame.Translate(closestCorner)
    return cornerFrame


def publishTriad(transform, collectionId=1234):

    o = lcmvs.obj_t()

    xyz = transform.GetPosition()
    rpy = transformUtils.rollPitchYawFromTransform(transform)

    o.roll, o.pitch, o.yaw = rpy
    o.x, o.y, o.z = xyz
    o.id = 1

    m = lcmvs.obj_collection_t()
    m.id = collectionId
    m.name = 'stance_triads'
    m.type = lcmvs.obj_collection_t.AXIS3D
    m.nobjs = 1
    m.reset = False
    m.objs = [o]

    lcmUtils.publish('OBJ_COLLECTION', m)


def createBlockAffordance(origin, xaxis, yaxis, zaxis, xwidth, ywidth, zwidth, name, parent='affordances'):

    cube = vtk.vtkCubeSource()
    cube.SetXLength(xwidth)
    cube.SetYLength(ywidth)
    cube.SetZLength(zwidth)
    cube.Update()
    cube = shallowCopy(cube.GetOutput())

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    obj = showPolyData(cube, name, cls=BlockAffordanceItem, parent=parent)
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())

    params = dict(origin=origin, xwidth=xwidth, ywidth=ywidth, zwidth=zwidth, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis, friendly_name=name)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()
    return obj


def segmentBlockByTopPlane(polyData, blockDimensions, expectedNormal, expectedXAxis, edgeSign=1, name='block affordance'):

    polyData, planeOrigin, normal  = applyPlaneFit(polyData, distanceThreshold=0.05, expectedNormal=expectedNormal, returnOrigin=True)

    _, lineDirection, _ = applyLineFit(polyData)

    zaxis = lineDirection
    yaxis = normal
    xaxis = np.cross(yaxis, zaxis)

    if np.dot(xaxis, expectedXAxis) < 0:
        xaxis *= -1

    # make right handed
    zaxis = np.cross(xaxis, yaxis)


    edgePoints = computeEdge(polyData, zaxis, xaxis*edgeSign)
    edgePoints = vtkNumpy.getVtkPolyDataFromNumpyPoints(edgePoints)

    d = DebugData()
    obj = updatePolyData(edgePoints, 'edge points', parent=getDebugFolder(), visible=False)

    linePoint, lineDirection, _ = applyLineFit(edgePoints)
    zaxis = lineDirection
    xaxis = np.cross(yaxis, zaxis)


    if np.dot(xaxis, expectedXAxis) < 0:
        xaxis *= -1

    # make right handed
    zaxis = np.cross(xaxis, yaxis)

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, xaxis, resultArrayName='dist_along_line')
    pts = vtkNumpy.getNumpyFromVtk(polyData, 'Points')

    dists = np.dot(pts-linePoint, zaxis)

    p1 = linePoint + zaxis*np.min(dists)
    p2 = linePoint + zaxis*np.max(dists)

    p1 = projectPointToPlane(p1, planeOrigin, normal)
    p2 = projectPointToPlane(p2, planeOrigin, normal)

    xwidth, ywidth = blockDimensions
    zwidth = np.linalg.norm(p2 - p1)

    origin = p1 - edgeSign*xaxis*xwidth/2.0 - yaxis*ywidth/2.0 + zaxis*zwidth/2.0

    d = DebugData()

    #d.addSphere(linePoint, radius=0.02)
    #d.addLine(linePoint, linePoint + yaxis*ywidth)
    #d.addLine(linePoint, linePoint + xaxis*xwidth)
    #d.addLine(linePoint, linePoint + zaxis*zwidth)


    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)

    d.addSphere(origin, radius=0.01)
    #d.addLine(origin - xaxis*xwidth/2.0, origin + xaxis*xwidth/2.0)
    #d.addLine(origin - yaxis*ywidth/2.0, origin + yaxis*ywidth/2.0)
    #d.addLine(origin - zaxis*zwidth/2.0, origin + zaxis*zwidth/2.0)

    d.addLine(origin, origin + xaxis*xwidth/2.0)
    d.addLine(origin, origin + yaxis*ywidth/2.0)
    d.addLine(origin, origin + zaxis*zwidth/2.0)


    #obj = updatePolyData(d.getPolyData(), 'block axes')
    #obj.setProperty('Color', QtGui.QColor(255, 255, 0))
    #obj.setProperty('Visible', False)

    obj = createBlockAffordance(origin, xaxis, yaxis, zaxis, xwidth, ywidth, zwidth, name)

    icpTransform = mapsregistrar.getInitialTransform()

    if icpTransform:
        t = obj.actor.GetUserTransform()
        objTrack = showPolyData(obj.polyData, name, cls=BlockAffordanceItem, parent=obj, color=[0.8, 1, 0.8])
        objTrack.actor.SetUserTransform(t)
        objTrack.baseTransform = vtk.vtkTransform()
        objTrack.baseTransform.SetMatrix(t.GetMatrix())
        objTrack.icpTransformInitial = icpTransform
        objTrack.addToView(app.getDRCView())

        print 'setting base transform:', objTrack.baseTransform.GetPosition()
        print 'setting initial icp:', objTrack.icpTransformInitial.GetPosition()

        mapsregistrar.addICPCallback(objTrack.updateICPTransform)


    frameObj = showFrame(obj.actor.GetUserTransform(), name + ' frame', parent=obj, scale=0.2, visible=False)
    frameObj.addToView(app.getDRCView())

    computeDebrisGraspSeed(obj)
    t = computeDebrisStanceFrame(obj)
    if t:
        showFrame(t, 'debris stance frame', parent=obj)
        obj.publishCallback = functools.partial(publishDebrisStanceFrame, obj)

    return obj


def computeDebrisGraspSeed(aff):

    debrisReferenceFrame = om.findObjectByName('debris reference frame')
    if debrisReferenceFrame:

        debrisReferenceFrame = debrisReferenceFrame.transform
        affCornerFrame = computeCornerFrame(aff, debrisReferenceFrame)
        showFrame(affCornerFrame, 'board corner frame', parent=aff, visible=False)


def computeDebrisStanceFrame(aff):

    debrisReferenceFrame = om.findObjectByName('debris reference frame')
    debrisWallEdge = om.findObjectByName('debris plane edge')

    if debrisReferenceFrame and debrisWallEdge:

        debrisReferenceFrame = debrisReferenceFrame.transform

        affGroundFrame = computeGroundFrame(aff, debrisReferenceFrame)

        updateFrame(affGroundFrame, 'board ground frame', parent=getDebugFolder(), visible=False)

        affWallEdge = computeGroundFrame(aff, debrisReferenceFrame)

        framePos = np.array(affGroundFrame.GetPosition())
        p1, p2 = debrisWallEdge.points
        edgeAxis = p2 - p1
        edgeAxis /= np.linalg.norm(edgeAxis)
        projectedPos = p1 + edgeAxis * np.dot(framePos - p1, edgeAxis)

        affWallFrame = vtk.vtkTransform()
        affWallFrame.PostMultiply()

        useWallFrameForRotation = True

        if useWallFrameForRotation:
            affWallFrame.SetMatrix(debrisReferenceFrame.GetMatrix())
            affWallFrame.Translate(projectedPos - np.array(debrisReferenceFrame.GetPosition()))

            stanceWidth = 0.20
            stanceOffsetX = -0.35
            stanceOffsetY = 0.45
            stanceRotation = 0.0

        else:
            affWallFrame.SetMatrix(affGroundFrame.GetMatrix())
            affWallFrame.Translate(projectedPos - framePos)

            stanceWidth = 0.20
            stanceOffsetX = -0.35
            stanceOffsetY = -0.45
            stanceRotation = math.pi/2.0

        stanceFrame, _, _ = getFootFramesFromReferenceFrame(affWallFrame, stanceWidth, math.degrees(stanceRotation), [stanceOffsetX, stanceOffsetY, 0.0])

        return stanceFrame


def publishDebrisStanceFrame(aff):
    frame = computeDebrisStanceFrame(aff)
    publishTriad(frame)


def segmentBlockByPlanes(blockDimensions):

    planes = om.getObjectChildren(om.findObjectByName('selected planes'))[:2]

    viewPlaneNormal = getSegmentationView().camera().GetViewPlaneNormal()
    origin1, normal1, plane1 = getPlaneEquationFromPolyData(planes[0].polyData, expectedNormal=viewPlaneNormal)
    origin2, normal2, plane2 = getPlaneEquationFromPolyData(planes[1].polyData, expectedNormal=viewPlaneNormal)

    xaxis = normal2
    yaxis = normal1
    zaxis = np.cross(xaxis, yaxis)
    xaxis = np.cross(yaxis, zaxis)

    pts1 = vtkNumpy.getNumpyFromVtk(planes[0].polyData, 'Points')
    pts2 = vtkNumpy.getNumpyFromVtk(planes[1].polyData, 'Points')

    linePoint = np.zeros(3)
    centroid2 = np.sum(pts2, axis=0)/len(pts2)
    vtk.vtkPlane.ProjectPoint(centroid2, origin1, normal1, linePoint)

    dists = np.dot(pts1-linePoint, zaxis)

    p1 = linePoint + zaxis*np.min(dists)
    p2 = linePoint + zaxis*np.max(dists)

    xwidth, ywidth = blockDimensions
    zwidth = np.linalg.norm(p2 - p1)

    origin = p1 + xaxis*xwidth/2.0 + yaxis*ywidth/2.0 + zaxis*zwidth/2.0 

    d = DebugData()

    d.addSphere(linePoint, radius=0.02)
    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)

    d.addSphere(origin, radius=0.01)
    d.addLine(origin - xaxis*xwidth/2.0, origin + xaxis*xwidth/2.0)
    d.addLine(origin - yaxis*ywidth/2.0, origin + yaxis*ywidth/2.0)
    d.addLine(origin - zaxis*zwidth/2.0, origin + zaxis*zwidth/2.0)
    obj = updatePolyData(d.getPolyData(), 'block axes')
    obj.setProperty('Color', QtGui.QColor(255, 255, 0))
    obj.setProperty('Visible', False)

    cube = vtk.vtkCubeSource()
    cube.SetXLength(xwidth)
    cube.SetYLength(ywidth)
    cube.SetZLength(zwidth)
    cube.Update()
    cube = shallowCopy(cube.GetOutput())

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(origin)

    obj = updatePolyData(cube, 'block affordance', cls=BlockAffordanceItem, parent='affordances')
    obj.actor.SetUserTransform(t)
    obj.addToView(app.getDRCView())

    params = dict(origin=origin, xwidth=xwidth, ywidth=ywidth, zwidth=zwidth, xaxis=xaxis, yaxis=yaxis, zaxis=zaxis)
    obj.setAffordanceParams(params)
    obj.updateParamsFromActorTransform()


def startBoundedPlaneSegmentation():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentBoundedPlaneByAnnotation)


def startValveSegmentationByWallPlane(expectedValveRadius):

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentValveByWallPlane, expectedValveRadius)


def startValveSegmentationManual(expectedValveRadius):

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentValve, expectedValveRadius)


def startRefitWall():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = refitWall



def startWyeSegmentation():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentWye)


def startDoorHandleSegmentation(otdfType):

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentDoorHandle, otdfType)


def startTrussSegmentation():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentTruss)


def startHoseNozzleSegmentation():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentHoseNozzle)


def storePoint(p):
    global _pickPoint
    _pickPoint = p


def getPickPoint():
    global _pickPoint
    return _pickPoint


def startPickPoint():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = storePoint


def startSelectToolTip():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = selectToolTip


def startDrillSegmentation():

    picker = PointPicker(numberOfPoints=3)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrill)


def startDrillAutoSegmentation():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrillAuto)


def startDrillBarrelSegmentation():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrillBarrel)


def startDrillWallSegmentation():

    picker = PointPicker(numberOfPoints=3)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrillWall)

def startDrillWallSegmentationConstrained(rightAngleLocation):

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = False
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrillWallConstrained, rightAngleLocation)

def startDrillInHandSegmentation():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.drawLines = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentDrillInHand)


def startSegmentDebrisWall():

    picker = PointPicker(numberOfPoints=1)
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentDebrisWall)

def startSegmentDebrisWallManual():

    picker = PointPicker(numberOfPoints=2)
    addViewPicker(picker)
    picker.enabled = True
    picker.start()
    picker.annotationFunc = functools.partial(segmentDebrisWallManual)


def selectToolTip(point1):
    print point1



def segmentDebrisWallManual(point1, point2):

    p1, p2 = point1, point2

    d = DebugData()
    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)
    edgeObj = updatePolyData(d.getPolyData(), 'debris plane edge', visible=True)
    edgeObj.points = [p1, p2]

    xaxis = p2 - p1
    xaxis /= np.linalg.norm(xaxis)
    zaxis = np.array([0.0, 0.0, 1.0])
    yaxis = np.cross(zaxis, xaxis)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(p1)

    updateFrame(t, 'debris plane frame', parent=edgeObj, visible=False)

    refFrame = vtk.vtkTransform()
    refFrame.PostMultiply()
    refFrame.SetMatrix(t.GetMatrix())
    refFrame.Translate(-xaxis + yaxis + zaxis*20.0)
    updateFrame(refFrame, 'debris reference frame', parent=edgeObj, visible=False)


def segmentDebrisWall(point1):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = shallowCopy(inputObj.polyData)

    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, distanceThreshold=0.02, expectedNormal=viewPlaneNormal, perpendicularAxis=viewPlaneNormal,
                                             searchOrigin=point1, searchRadius=0.25, angleEpsilon=0.7, returnOrigin=True)


    planePoints = thresholdPoints(polyData, 'dist_to_plane', [-0.02, 0.02])
    updatePolyData(planePoints, 'unbounded plane points', parent=getDebugFolder(), visible=False)


    planePoints = applyVoxelGrid(planePoints, leafSize=0.03)
    planePoints = labelOutliers(planePoints, searchRadius=0.06, neighborsInSearchRadius=10)

    updatePolyData(planePoints, 'voxel plane points', parent=getDebugFolder(), colorByName='is_outlier', visible=False)

    planePoints = thresholdPoints(planePoints, 'is_outlier', [0, 0])

    planePoints = labelDistanceToPoint(planePoints, point1)
    clusters = extractClusters(planePoints, clusterTolerance=0.10)
    clusters.sort(key=lambda x: vtkNumpy.getNumpyFromVtk(x, 'distance_to_point').min())

    planePoints = clusters[0]
    planeObj = updatePolyData(planePoints, 'debris plane points', parent=getDebugFolder(), visible=False)


    perpAxis = [0,0,-1]
    perpAxis /= np.linalg.norm(perpAxis)
    edgeAxis = np.cross(normal, perpAxis)

    edgePoints = computeEdge(planePoints, edgeAxis, perpAxis)
    edgePoints = vtkNumpy.getVtkPolyDataFromNumpyPoints(edgePoints)
    updatePolyData(edgePoints, 'edge points', parent=getDebugFolder(), visible=False)


    linePoint, lineDirection, _ = applyLineFit(edgePoints)

    #binCounts = computePointCountsAlongAxis(planePoints, lineDirection)


    xaxis = lineDirection
    yaxis = normal

    zaxis = np.cross(xaxis, yaxis)

    if np.dot(zaxis, [0, 0, 1]) < 0:
        zaxis *= -1
        xaxis *= -1

    pts = vtkNumpy.getNumpyFromVtk(planePoints, 'Points')

    dists = np.dot(pts-linePoint, xaxis)

    p1 = linePoint + xaxis*np.min(dists)
    p2 = linePoint + xaxis*np.max(dists)

    p1 = projectPointToPlane(p1, origin, normal)
    p2 = projectPointToPlane(p2, origin, normal)

    d = DebugData()
    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)
    edgeObj = updatePolyData(d.getPolyData(), 'debris plane edge', parent=planeObj, visible=True)
    edgeObj.points = [p1, p2]

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate(p1)

    updateFrame(t, 'debris plane frame', parent=planeObj, visible=False)

    refFrame = vtk.vtkTransform()
    refFrame.PostMultiply()
    refFrame.SetMatrix(t.GetMatrix())
    refFrame.Translate(-xaxis + yaxis + zaxis*20.0)
    updateFrame(refFrame, 'debris reference frame', parent=planeObj, visible=False)


def segmentBoundedPlaneByAnnotation(point1, point2):

    inputObj = om.findObjectByName('pointcloud snapshot')
    polyData = shallowCopy(inputObj.polyData)


    viewPlaneNormal = np.array(getSegmentationView().camera().GetViewPlaneNormal())

    polyData, origin, normal = applyPlaneFit(polyData, distanceThreshold=0.015, expectedNormal=viewPlaneNormal, perpendicularAxis=viewPlaneNormal,
                                             searchOrigin=point1, searchRadius=0.3, angleEpsilon=0.7, returnOrigin=True)


    planePoints = thresholdPoints(polyData, 'dist_to_plane', [-0.015, 0.015])
    updatePolyData(planePoints, 'unbounded plane points', parent=getDebugFolder(), visible=False)


    planePoints = applyVoxelGrid(planePoints, leafSize=0.03)
    planePoints = labelOutliers(planePoints, searchRadius=0.06, neighborsInSearchRadius=12)

    updatePolyData(planePoints, 'voxel plane points', parent=getDebugFolder(), colorByName='is_outlier', visible=False)

    planePoints = thresholdPoints(planePoints, 'is_outlier', [0, 0])

    planePoints = labelDistanceToPoint(planePoints, point1)
    clusters = extractClusters(planePoints, clusterTolerance=0.10)
    clusters.sort(key=lambda x: vtkNumpy.getNumpyFromVtk(x, 'distance_to_point').min())

    planePoints = clusters[0]
    updatePolyData(planePoints, 'plane points', parent=getDebugFolder(), visible=False)


    perpAxis = point2 - point1
    perpAxis /= np.linalg.norm(perpAxis)
    edgeAxis = np.cross(normal, perpAxis)

    edgePoints = computeEdge(planePoints, edgeAxis, perpAxis)
    edgePoints = vtkNumpy.getVtkPolyDataFromNumpyPoints(edgePoints)
    updatePolyData(edgePoints, 'edge points', parent=getDebugFolder(), visible=False)


    linePoint, lineDirection, _ = applyLineFit(edgePoints)

    zaxis = normal
    yaxis = lineDirection
    xaxis = np.cross(yaxis, zaxis)

    if np.dot(xaxis, perpAxis) < 0:
        xaxis *= -1

    # make right handed
    yaxis = np.cross(zaxis, xaxis)

    pts = vtkNumpy.getNumpyFromVtk(planePoints, 'Points')

    dists = np.dot(pts-linePoint, yaxis)

    p1 = linePoint + yaxis*np.min(dists)
    p2 = linePoint + yaxis*np.max(dists)

    p1 = projectPointToPlane(p1, origin, normal)
    p2 = projectPointToPlane(p2, origin, normal)

    d = DebugData()
    d.addSphere(p1, radius=0.01)
    d.addSphere(p2, radius=0.01)
    d.addLine(p1, p2)
    updatePolyData(d.getPolyData(), 'plane edge', parent=getDebugFolder(), visible=False)

    t = getTransformFromAxes(xaxis, yaxis, zaxis)
    t.PostMultiply()
    t.Translate((p1 + p2)/ 2.0)

    updateFrame(t, 'plane edge frame', parent=getDebugFolder(), visible=False)



savedCameraParams = None

def perspective():

    global savedCameraParams
    if savedCameraParams is None:
        return

    aff = getDefaultAffordanceObject()
    if aff:
        aff.setProperty('Alpha', 1.0)

    obj = om.findObjectByName('pointcloud snapshot')
    if obj is not None:
        obj.actor.SetPickable(1)

    view = getSegmentationView()
    c = view.camera()
    c.ParallelProjectionOff()
    c.SetPosition(savedCameraParams['Position'])
    c.SetFocalPoint(savedCameraParams['FocalPoint'])
    c.SetViewUp(savedCameraParams['ViewUp'])
    view.setCameraManipulationStyle()
    view.render()


def saveCameraParams(overwrite=False):

    global savedCameraParams
    if overwrite or (savedCameraParams is None):

        view = getSegmentationView()
        c = view.camera()
        savedCameraParams = dict(Position=c.GetPosition(), FocalPoint=c.GetFocalPoint(), ViewUp=c.GetViewUp())



def getDefaultAffordanceObject():

    obj = om.getActiveObject()
    if isinstance(obj, AffordanceItem):
        return obj

    for obj in om.objects.values():
        if isinstance(obj, AffordanceItem):
            return obj

def getVisibleRobotModel():
    for obj in om.objects.values():
        if isinstance(obj, om.RobotModelItem) and obj.getProperty('Visible'):
            return obj

def orthoX():

    aff = getDefaultAffordanceObject()
    if not aff:
        return

    saveCameraParams()

    aff.updateParamsFromActorTransform()
    aff.setProperty('Alpha', 0.3)
    om.findObjectByName('pointcloud snapshot').actor.SetPickable(0)

    view = getSegmentationView()
    c = view.camera()
    c.ParallelProjectionOn()

    origin = aff.params['origin']
    viewDirection = aff.params['xaxis']
    viewUp = -aff.params['yaxis']
    viewDistance = aff.params['xwidth']*3
    scale = aff.params['zwidth']

    c.SetFocalPoint(origin)
    c.SetPosition(origin - viewDirection*viewDistance)
    c.SetViewUp(viewUp)
    c.SetParallelScale(scale)

    view.setActorManipulationStyle()
    view.render()


def orthoY():

    aff = getDefaultAffordanceObject()
    if not aff:
        return

    saveCameraParams()

    aff.updateParamsFromActorTransform()
    aff.setProperty('Alpha', 0.3)
    om.findObjectByName('pointcloud snapshot').actor.SetPickable(0)

    view = getSegmentationView()
    c = view.camera()
    c.ParallelProjectionOn()

    origin = aff.params['origin']
    viewDirection = aff.params['yaxis']
    viewUp = -aff.params['xaxis']
    viewDistance = aff.params['ywidth']*4
    scale = aff.params['zwidth']

    c.SetFocalPoint(origin)
    c.SetPosition(origin - viewDirection*viewDistance)
    c.SetViewUp(viewUp)
    c.SetParallelScale(scale)

    view.setActorManipulationStyle()
    view.render()


def orthoZ():

    aff = getDefaultAffordanceObject()
    if not aff:
        return

    saveCameraParams()

    aff.updateParamsFromActorTransform()
    aff.setProperty('Alpha', 0.3)
    om.findObjectByName('pointcloud snapshot').actor.SetPickable(0)

    view = getSegmentationView()
    c = view.camera()
    c.ParallelProjectionOn()

    origin = aff.params['origin']
    viewDirection = aff.params['zaxis']
    viewUp = -aff.params['yaxis']
    viewDistance = aff.params['zwidth']
    scale = aff.params['ywidth']*6

    c.SetFocalPoint(origin)
    c.SetPosition(origin - viewDirection*viewDistance)
    c.SetViewUp(viewUp)
    c.SetParallelScale(scale)

    view.setActorManipulationStyle()
    view.render()


def zoomToDisplayPoint(displayPoint, boundsRadius=0.5, view=None):

    pickedPoint = pickPoint(displayPoint, obj='pointcloud snapshot')
    if pickedPoint is None:
        return

    view = view or app.getCurrentRenderView()

    worldPt1, worldPt2 = getRayFromDisplayPoint(getSegmentationView(), displayPoint)

    diagonal = np.array([boundsRadius, boundsRadius, boundsRadius])
    bounds = np.hstack([pickedPoint - diagonal, pickedPoint + diagonal])
    bounds = [bounds[0], bounds[3], bounds[1], bounds[4], bounds[2], bounds[5]]
    view.renderer().ResetCamera(bounds)
    view.camera().SetFocalPoint(pickedPoint)
    view.render()


def extractPointsAlongClickRay(displayPoint, distanceToLineThreshold=0.3, addDebugRay=False):

    worldPt1, worldPt2 = getRayFromDisplayPoint(getSegmentationView(), displayPoint)

    if showDebugRay:
        d = DebugData()
        d.addLine(worldPt1, worldPt2)
        showPolyData(d.getPolyData(), 'mouse click ray', visible=False)


    segmentationObj = om.findObjectByName('pointcloud snapshot')
    polyData = segmentationObj.polyData

    polyData = labelDistanceToLine(polyData, worldPt1, worldPt2)

    # extract points near line
    polyData = thresholdPoints(polyData, 'distance_to_line', [0.0, distanceToLineThreshold])
    showPolyData(polyData, 'selected cluster', colorByName='distance_to_line', visible=False)
    return polyData


def extractPointsAlongClickRay(position, ray):

    print 'extractPointsAlongClickRay'

    segmentationObj = om.findObjectByName('pointcloud snapshot')
    polyData = segmentationObj.polyData

    polyData = labelDistanceToLine(polyData, position, position + ray)

    distanceToLineThreshold = 0.05

    # extract points near line
    polyData = thresholdPoints(polyData, 'distance_to_line', [0.0, distanceToLineThreshold])

    polyData = pointCloudUtils.labelPointDistanceAlongAxis(polyData, ray, origin=position, resultArrayName='dist_along_line')
    polyData = thresholdPoints(polyData, 'dist_along_line', [0.20, 1e6])

    showPolyData(polyData, 'ray points', colorByName='distance_to_line', view=getSegmentationView())

    dists = vtkNumpy.getNumpyFromVtk(polyData, 'distance_to_line')
    points = vtkNumpy.getNumpyFromVtk(polyData, 'Points')

    d = DebugData()
    d.addSphere(points[dists.argmin()], radius=0.01)
    d.addLine(position, points[dists.argmin()])
    showPolyData(d.getPolyData(), 'camera ray', view=getSegmentationView())
