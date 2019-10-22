#!/usr/bin/env python

import os
import shutil
import sys
import re
import glob
import subprocess
import requests
import json
import time
from datetime import datetime
from datetime import timedelta
from pytz import timezone
from optparse import OptionParser

CONFIG_FILE = 'config.json'
MONITOR_PROGRAM = './watchdog-monitor.js'

# Usage for this program
sUSAGE = """Usage: %s 
   -s, --slack      provide a Slack message when completed
   -f, --slackfail  report a failure (but not success) to Slack 
 """ % (os.path.basename(__file__))
    

def printStdErr(sOutput):
    sys.stderr.write(sOutput + "\n")


def errorMsg(sMsg):
    printStdErr("Error: " + sMsg)
    sys.exit(-1)


# Need subclass to avoid error message
class SimpleOptionParser (OptionParser):

    def error(self, msg):
        print(sUSAGE)
        sys.exit(-1)


def usageMsg(sError = None):
    if sError is not None:
        printStdErr("\nError: " + sError + "\n")
    printStdErr(sUSAGE)
    sys.exit(-1)


def getConfig(sSection, sKey):
    """Get a configuration value"""
    if not hasattr(getDates, 'aConfig'):
        if not os.path.isfile(CONFIG_FILE):
            errorMsg('No configuration file exists: %s' % CONFIG_FILE)
        with open(CONFIG_FILE) as oFile:
            getConfig.aConfig = json.load(oFile)
    try:
        return getConfig.aConfig[sSection][sKey]
    except KeyError:
        errorMsg('invalid configuration element: %s => %s' % (sSection, sKey))


def getDates():
    """Get local and GMT time objects"""
    if not hasattr(getDates, 'aDates'):
        getDates.aDates = {
            'local': datetime.now(timezone(getConfig('time', 'zone_local'))),
            'gmt': datetime.now(timezone(getConfig('time', 'zone_global')))
        }
    return getDates.aDates


def postToCloudWatch(sServerId, sMetricName, sServerDesc, sStatus):
    """Post metrics to AWS CloudWatch"""
    import boto3.ec2
    if not hasattr(postToCloudWatch, 'oBoto'):
        postToCloudWatch.oBoto = boto3.client('cloudwatch',
                                              aws_access_key_id=getConfig('cloudwatch', 'access_id'),
                                              aws_secret_access_key=getConfig('cloudwatch', 'secret_key'))
    sNamespace = 'EC2: ' + sServerDesc
    print('Posting to EC2 CloudWatch: namespace: %s, metric: %s, instance: %s, value: %s' %
          (sNamespace, sMetricName, sServerId, sStatus))
    postToCloudWatch.oBoto.put_metric_data(sNamespace, sMetricName, value=sStatus, timestamp=None,
                                           unit='Count', dimensions={'InstanceId' : [sServerId]})


def processError(sMsg, sOutput):
    """Handle any process error"""
    if sOutput == "":
        errorMsg(sMsg)
    else:
        errorMsg(sMsg + ":\n  " + '  '.join(sOutput.splitlines(True)))
    

def shellCommand(aCommand, bDisplay = True, bIgnoreFailure = False):
    """Run a shell command with killing if process runs too long"""

    sOutput = ""
    oProc = subprocess.Popen(aCommand, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    try:
        if bDisplay:
            print("")
            print(" ".join(aCommand))

        iSeconds = 0
        iTimeoutSecs = int(getConfig('process', 'timeout_secs'))
        iPollSecs = int(getConfig('process', 'poll_secs'))
        while True:
            if iSeconds >= iTimeoutSecs:
                oProc.terminate()
                return "watchdog process timed out: " + sOutput

            sLine = oProc.stdout.readline()
            sOutput += sLine.decode("utf-8")
            iReturnCode = oProc.poll()
            if iReturnCode is not None:
                for sLine in oProc.stdout.readlines():
                    sOutput += sLine.decode("utf-8")
                if bDisplay:
                    print(sOutput)
                break
            time.sleep(iPollSecs)
            iSeconds += iPollSecs

        if bIgnoreFailure is False and iReturnCode != 0:
            processError("watchdog command failed", sOutput)
        return sOutput

    except KeyboardInterrupt:
        print("")
        printStdErr("watchdog command stopped by keyboard interrupt, stopping...")
        sys.exit(-1)

    except OSError:
        processError("watchdog command failed", sOutput)
        return sOutput


def fileGetContents(sFilename):
    """Get content of a file"""
    with open(sFilename) as f:
        return f.read()

    
def filePutContents(sFilename, sContent):
    """Save content to a file"""
    f = open(sFilename, 'w')
    f.write(sContent)
    f.close()

    
def slackMessage(sMessage):
    """Post a message to Slack"""
    requests.post(getConfig('slack', 'url'), data=json.dumps({'text':       sMessage,
                                                              'channel':    '#' + getConfig('slack', 'channel'),
                                                              'user':       getConfig('slack', 'user'),
                                                              'icon_emoji': getConfig('slack', 'emoji')}))

    
def createTargetDir():
    """Create a writeable directory with the date/time information"""
    sLogDir = getConfig('system', 'log_dir')
    if not os.path.isdir(sLogDir):
        usageMsg("log directory does not exist: " + sLogDir)

    # Target directory combines all pieces including the date/time
    sTargetDir = "%s/%s" % (sLogDir, getDates()['gmt'].strftime("%Y_%m_%d-%H_%M_%S_%Z"))
    
    try:
        os.makedirs(sTargetDir)
    except OSError:
        errorMsg("unable to create target directory: " + sTargetDir)
    return sTargetDir


# Remove old log directories 
def cleanUpLogs(sTargetDir):
    sLogDir = os.path.dirname(sTargetDir)

    # Clean up the target directory if empty
    if not os.listdir(sTargetDir):
        print("Log directory is empty, removing: " + os.path.basename(sTargetDir))
        os.rmdir(sTargetDir)

    # Clean up any old directories
    oTimeout = timedelta(days=int(getConfig('system', 'expire_log_days')))
    for sDirPath, aDirNames, aFileNames in os.walk(sLogDir):
        for sDir in aDirNames:
            sCurPath = os.path.join(sDirPath, sDir)
            modified = datetime.fromtimestamp(os.path.getmtime(sCurPath))
            if datetime.now() - modified > oTimeout:
                shutil.rmtree(sCurPath)
        break


# Main method for running codeception
def runWatchdog(sTargetDir, aOptions):

    # Prepare the args for running the monitor
    aArgs = [MONITOR_PROGRAM, sTargetDir]

    # Clearing cookies before running
    sCookiePath = getConfig('cookies', 'path')
    if aOptions.bClearCookies and os.path.isfile(sCookiePath):
        os.remove(sCookiePath)

    # Record the time before running
    fTimeStart = time.time()
    sOutput    = shellCommand(aArgs, True, True)
    sTimeTotal = str(round(time.time() - fTimeStart, 1))

    # Determine if failed or not - failure time is abnormally high
    sReportLink = None
    sSuccessStr = getConfig('application', 'success_string')
    if re.search(sSuccessStr, sOutput) is not None:
        for sFile in glob.glob(sTargetDir + "/*.jpeg"):
            os.remove(sFile)
        bSuccess = True
        print("Website watchdog completed successfully, taking %s seconds" % sTimeTotal)
    else:
        bSuccess = False
        sMsg = "Website watchdog FAILED, taking %s seconds" % sTimeTotal
        print(sMsg)
        filePutContents(sTargetDir + "/" + 'output.txt', sMsg + "\n" + sOutput)
        sTimeTotal = getConfig('cloudwatch', 'failure_time')
        sReportLink = buildReportDir(sTargetDir)

    # Message to be sent to slack
    if aOptions.bSlack or aOptions.bSlackFailure:
        if bSuccess and aOptions.bSlack:
            slackMessage("Website watchdog completed successfully")
        elif bSuccess is False and sReportLink:
            slackMessage("Website watchdog FAILED - see: " + sReportLink)

    # Reporting to CloudWatch
    if aOptions.bCloudwatch:
        postToCloudWatch(getConfig('cloudwatch', 'server_id'), getConfig('cloudwatch', 'metric_watchdog'),
                         getConfig('cloudwatch', 'server_name'), sTimeTotal)


def buildReportDir(sTargetDir):
    """Return a string with all report URLs"""
    sBaseUrl = getConfig('system', 'report_url') + os.path.basename(sTargetDir)
    aLinks = []
    for sFile in glob.glob(sTargetDir + '/*'):
        sBase = os.path.basename(sFile)
        aLinks.append('<li><a href="%s">%s</li>' % (sBase, sBase))
        
    # Use local and GTM time
    aDates     = getDates()
    sDateLocal = aDates['local'].strftime("%B %d, %Y %H:%M:%S %Z")
    sDateGmt   = aDates['gmt'].strftime("%B %d, %Y %H:%M:%S %Z")
    
    # Create the index file
    sList = "\n".join(aLinks)
    sHtml  = """
<html>
<title>Watchdog - %s</title>
<body>
    <h1>Watchdog Log</h1>
    <h2>Date local: %s</h2>
    <h2>Date GMT: %s</h2>
    <ul>%s</ul>
</body><html>""" % (sDateLocal, sDateLocal, sDateGmt, sList)
    filePutContents(sTargetDir + "/index.html", sHtml)
    
    return sBaseUrl


def main():
    """Primary execution"""

    # Go to the directory of the script (eliminate /private from MacOS)
    sScriptDir = re.sub('^/private', '', os.path.dirname(os.path.abspath(__file__)))
    os.chdir(sScriptDir)

    oParser = SimpleOptionParser(sUSAGE)
    oParser.add_option("-c", "--cloudwatch", action="store_true", dest="bCloudwatch")
    oParser.add_option("-s", "--slack",      action="store_true", dest="bSlack")
    oParser.add_option("-f", "--slackfail",  action="store_true", dest="bSlackFailure")
    oParser.add_option("-d", "--clear-cookies",  action="store_true", dest="bClearCookies")
    (aOptions, aArgs) = oParser.parse_args()

    # Create the target directory
    sTargetDir = createTargetDir()

    # Run watchdog with any passed options
    runWatchdog(sTargetDir, aOptions)

    # Clean up old logs
    cleanUpLogs(sTargetDir)
    

# Primary execution
if __name__ == "__main__":
    main()
