#!/usr/bin/env python3
# coding: utf-8 -*-
#
# Author: zaraki673 & pipiche38
#
"""
    Module: heartbeat.py

    Description: Manage all actions done during the onHeartbeat() call

"""

import time
import datetime

import Domoticz
from Zigbee.zdpCommands import (zdp_IEEE_address_request,
                                zdp_node_descriptor_request,
                                zdp_NWK_address_request)

from Modules.basicOutputs import getListofAttribute
from Modules.casaia import pollingCasaia
from Modules.danfoss import danfoss_room_sensor_polling
from Modules.domoTools import timedOutDevice
from Modules.pairingProcess import (binding_needed_clusters_with_zigate,
                                    processNotinDBDevices)
from Modules.paramDevice import sanity_check_of_param
from Modules.pluginDbAttributes import STORE_CONFIGURE_REPORTING
from Modules.readAttributes import (READ_ATTRIBUTES_REQUEST,
                                    ReadAttributeRequest_0b04_050b_0505_0508,
                                    ReadAttributeRequest_0702_0000,
                                    ReadAttributeRequest_0001,
                                    ReadAttributeRequest_0006_0000,
                                    ReadAttributeRequest_0008_0000,
                                    ReadAttributeRequest_0101_0000,
                                    ReadAttributeRequest_0102_0008,
                                    ReadAttributeRequest_0201_0012,
                                    ReadAttributeRequest_0402,
                                    ReadAttributeRequest_0405,
                                    ReadAttributeRequest_0702_PC321,
                                    ReadAttributeRequest_0702_ZLinky_TIC,
                                    ReadAttributeReq_ZLinky,
                                    ReadAttribute_ZLinkyIndex,
                                    ReadAttributeReq_Scheduled_ZLinky,
                                    ReadAttributeRequest_ff66,
                                    ping_device_with_read_attribute,
                                    ping_tuya_device)
from Modules.schneider_wiser import schneiderRenforceent
from Modules.tools import (ReArrangeMacCapaBasedOnModel, deviceconf_device,
                           get_device_nickname, getListOfEpForCluster, is_hex,
                           is_time_to_perform_work, mainPoweredDevice,
                           night_shift_jobs, removeNwkInList)
from Modules.zb_tables_management import mgmt_rtg, mgtm_binding
from Modules.tuyaTRV import tuya_switch_online

from Modules.zigateConsts import HEARTBEAT, MAX_LOAD_ZIGATE

# Read Attribute trigger: Every 10"
# Configure Reporting trigger: Every 15
# Network Topology start: 15' after plugin start
# Network Energy start: 30' after plugin start
# Legrand re-enforcement: Every 5'


QUIET_AFTER_START = (60 // HEARTBEAT)  # Quiet periode after a plugin start
NETWORK_TOPO_START = (900 // HEARTBEAT)
NETWORK_ENRG_START = (1800 // HEARTBEAT)
READATTRIBUTE_FEQ = (10 // HEARTBEAT)  # 10seconds ...
CONFIGURERPRT_FEQ = (( 30 // HEARTBEAT) + 1)
LEGRAND_FEATURES = (( 300 // HEARTBEAT ) + 3)
SCHNEIDER_FEATURES = (( 300 // HEARTBEAT) + 5)
BINDING_TABLE_REFRESH = (( 3600 // HEARTBEAT ) + 11)
NODE_DESCRIPTOR_REFRESH = (( 3600 // HEARTBEAT) + 13)
ATTRIBUTE_DISCOVERY_REFRESH = (( 3600 // HEARTBEAT ) + 7)
CHECKING_DELAY_READATTRIBUTE = (( 60 // HEARTBEAT ) + 7)


def attributeDiscovery(self, NwkId):

    rescheduleAction = False
    # If Attributes not yet discovered, let's do it

    if "ConfigSource" not in self.ListOfDevices[NwkId]:
        return False

    if self.ListOfDevices[NwkId]["ConfigSource"] == "DeviceConf":
        return False

    if "Attributes List" in self.ListOfDevices[NwkId] and len(self.ListOfDevices[NwkId]["Attributes List"]) > 0:
        return False

    if "Attributes List" not in self.ListOfDevices[NwkId]:
        self.ListOfDevices[NwkId]["Attributes List"] = {'Ep': {}}
    if "Request" not in self.ListOfDevices[NwkId]["Attributes List"]:
        self.ListOfDevices[NwkId]["Attributes List"]["Request"] = {}

    for iterEp in list(self.ListOfDevices[NwkId]["Ep"]):
        if iterEp == "ClusterType":
            continue
        if iterEp not in self.ListOfDevices[NwkId]["Attributes List"]["Request"]:
            self.ListOfDevices[NwkId]["Attributes List"]["Request"][iterEp] = {}

        for iterCluster in list(self.ListOfDevices[NwkId]["Ep"][iterEp]):
            if iterCluster in ("Type", "ClusterType", "ColorMode"):
                continue
            if iterCluster not in self.ListOfDevices[NwkId]["Attributes List"]["Request"][iterEp]:
                self.ListOfDevices[NwkId]["Attributes List"]["Request"][iterEp][iterCluster] = 0

            if self.ListOfDevices[NwkId]["Attributes List"]["Request"][iterEp][iterCluster] != 0:
                continue

            if not self.busy and self.ControllerLink.loadTransmit() <= MAX_LOAD_ZIGATE:
                if int(iterCluster, 16) < 0x0FFF:
                    getListofAttribute(self, NwkId, iterEp, iterCluster)
                    # getListofAttributeExtendedInfos(self, nwkid, EpOut, cluster, start_attribute=None, manuf_specific=None, manuf_code=None)
                elif (
                    "Manufacturer" in self.ListOfDevices[NwkId]
                    and len(self.ListOfDevices[NwkId]["Manufacturer"]) == 4
                    and is_hex(self.ListOfDevices[NwkId]["Manufacturer"])
                ):
                    getListofAttribute(
                        self,
                        NwkId,
                        iterEp,
                        iterCluster,
                        manuf_specific="01",
                        manuf_code=self.ListOfDevices[NwkId]["Manufacturer"],
                    )
                    # getListofAttributeExtendedInfos(self, nwkid, EpOut, cluster, start_attribute=None, manuf_specific=None, manuf_code=None)

                self.ListOfDevices[NwkId]["Attributes List"]["Request"][iterEp][iterCluster] = time.time()

            else:
                rescheduleAction = True

    return rescheduleAction


def ManufSpecOnOffPolling(self, NwkId):
    ReadAttributeRequest_0006_0000(self, NwkId)
    ReadAttributeRequest_0008_0000(self, NwkId)

def tuya_trv5_polling(self, NwkId):
    tuya_switch_online(self, NwkId, 0x01)
    
def check_delay_readattributes( self, NwkId ):
    
    if 'DelayReadAttributes' not in self.ListOfDevices[ NwkId ]:
        return
    
    if time.time() < self.ListOfDevices[ NwkId ]['DelayReadAttributes']['TargetTime']:
        return
    
    for cluster in list(self.ListOfDevices[ NwkId ]['DelayReadAttributes']['Clusters']):
        if self.busy or self.ControllerLink.loadTransmit() > MAX_LOAD_ZIGATE:
            return
        func = READ_ATTRIBUTES_REQUEST[cluster][0]
        func(self, NwkId)
        self.ListOfDevices[ NwkId ]['DelayReadAttributes']['Clusters'].remove( cluster )
        
    if len(self.ListOfDevices[ NwkId ]['DelayReadAttributes']['Clusters']) == 0:
        del self.ListOfDevices[ NwkId ]['DelayReadAttributes']
            
        
def check_delay_binding( self, NwkId, model ):
    # Profalux is the first one, but could get others
    # At pairing we need to leave time for the remote to get binded to the VR
    # Once it is done, then we can overwrite the binding

    if "DelayBindingAtPairing" in self.ListOfDevices[ NwkId ] and self.ListOfDevices[ NwkId ]["DelayBindingAtPairing"] == "Completed":
        self.log.logging( "Heartbeat", "Debug", "check_delay_binding -  %s DelayBindingAtPairing: %s" % (
            NwkId, self.ListOfDevices[ NwkId ]["DelayBindingAtPairing"]), NwkId, )
        return
    
    if model in ( "", {}):
        self.log.logging( "Heartbeat", "Debug", "check_delay_binding -  %s model: %s" % (
            NwkId, model), NwkId, )
        return

    if model not in self.DeviceConf or "DelayBindingAtPairing" not in self.DeviceConf[ model ] or self.DeviceConf[ model ]["DelayBindingAtPairing"] == 0:
        self.log.logging( "Heartbeat", "Debug", "check_delay_binding -  %s not applicable" % (
            NwkId), NwkId, )
        return
    
    if "ClusterToBind" not in self.DeviceConf[ model ] or len(self.DeviceConf[ model ]["ClusterToBind"]) == 0:
        self.log.logging( "Heartbeat", "Debug", "check_delay_binding -  %s Empty ClusterToBind" % (
            NwkId), NwkId, )
        return
    
    # We have a good candidate
    # We reached that step, because we have DelayindingAtPairing enabled and the BindTable is not empty.
    # Let's bind
    if self.configureReporting:
        if "Bind" in self.ListOfDevices[ NwkId ]:
            del self.ListOfDevices[ NwkId ]["Bind"]
            self.ListOfDevices[ NwkId ]["Bind"] = {}
        if STORE_CONFIGURE_REPORTING in self.ListOfDevices[ NwkId ]:
            del self.ListOfDevices[ NwkId ][STORE_CONFIGURE_REPORTING]
            self.ListOfDevices[ NwkId ]["Bind"] = {} 
        self.log.logging( "Heartbeat", "Debug", "check_delay_binding -  %s request Configure Reporting (and so bindings)" % (
            NwkId), NwkId, )
        binding_needed_clusters_with_zigate(self, NwkId)
        self.configureReporting.processConfigureReporting( NwkId=NwkId ) 
        self.ListOfDevices[ NwkId ]["DelayBindingAtPairing"] = "Completed"

        
    
def pollingManufSpecificDevices(self, NwkId, HB):

    FUNC_MANUF = {
        "TuyaTRV5Polling": tuya_trv5_polling,
        "ZLinkyPolling0702": ReadAttributeRequest_0702_ZLinky_TIC,
        "ZLinkyPollingGlobal": ReadAttributeReq_ZLinky,
        "PollingCusterff66": ReadAttributeRequest_ff66,
        "OnOffPollingFreq": ManufSpecOnOffPolling,
        "PowerPollingFreq": ReadAttributeRequest_0b04_050b_0505_0508,
        "MeterPollingFreq": ReadAttributeRequest_0702_0000,
        "PC321PollingFreq": ReadAttributeRequest_0702_PC321,
        "AC201Polling": pollingCasaia,
        "TuyaPing": ping_tuya_device,
        "BatteryPollingFreq": ReadAttributeRequest_0001,
        "DanfossRoomFreq": danfoss_room_sensor_polling,
        "TempPollingFreq": ReadAttributeRequest_0402,
        "HumiPollingFreq": ReadAttributeRequest_0405,
        "BattPollingFreq": ReadAttributeRequest_0001,
        "ZLinkyIndexes": ReadAttributeReq_Scheduled_ZLinky,      # Based on a specific time
        "ZLinkyPollingPTEC": ReadAttributeReq_Scheduled_ZLinky   # Every 15' by default
    }

    if "Param" not in self.ListOfDevices[NwkId]:
        return False

    if self.busy or self.ControllerLink.loadTransmit() > MAX_LOAD_ZIGATE:
        return True

    self.log.logging(
        "Heartbeat",
        "Debug",
        "++ pollingManufSpecificDevices -  %s " % (NwkId,),
        NwkId,
    )

    for param in self.ListOfDevices[NwkId]["Param"]:
        if param == "ZLinkyPollingPTEC":
            # We are requesting to execute at a particular time
            _current_time = datetime.datetime.now().strftime("%H:%M" )
            _target_time = self.ListOfDevices[NwkId]["Param"][ param ]
            self.log.logging(
                "Heartbeat",
                "Debug",
                "++ pollingManufSpecificDevices -  %s ScheduledZLinkyRead: Current: %s Target: %s"
                % (NwkId,_current_time, _target_time  ),
                NwkId,
            )

            if _current_time == _target_time and "ScheduledZLinkyRead" not in self.ListOfDevices[ NwkId ]:
                self.ListOfDevices[ NwkId ][ "ScheduledZLinkyRead" ] = True
                ReadAttributeReq_Scheduled_ZLinky( self, NwkId)

            elif _current_time != _target_time and "ScheduledZLinkyRead" in self.ListOfDevices[ NwkId ]:
                del self.ListOfDevices[ NwkId ][ "ScheduledZLinkyRead" ]

        elif param in FUNC_MANUF:
            _FEQ = self.ListOfDevices[NwkId]["Param"][param] // HEARTBEAT
            if _FEQ == 0:  # Disable
                continue
            self.log.logging(
                "Heartbeat",
                "Debug",
                "++ pollingManufSpecificDevices -  %s Found: %s=%s HB: %s FEQ: %s Cycle: %s"
                % (NwkId, param, self.ListOfDevices[NwkId]["Param"][param], HB, _FEQ, (HB % _FEQ)),
                NwkId,
            )
            if _FEQ and ((HB % _FEQ) != 0):
                continue
            self.log.logging(
                "Heartbeat",
                "Debug",
                "++ pollingManufSpecificDevices -  %s Found: %s=%s" % (NwkId, param, self.ListOfDevices[NwkId]["Param"][param]),
                NwkId,
            )

            func = FUNC_MANUF[param]
            func(self, NwkId)

    return False


def pollingDeviceStatus(self, NwkId):
    # """
    # Purpose is to trigger ReadAttrbute 0x0006 and 0x0008 on attribute 0x0000 if applicable
    # """

    if self.busy or self.ControllerLink.loadTransmit() > MAX_LOAD_ZIGATE:
        return True
    self.log.logging("Heartbeat", "Debug", "--------> pollingDeviceStatus Device %s" % NwkId, NwkId)
    if len(getListOfEpForCluster(self, NwkId, "0006")) != 0:
        ReadAttributeRequest_0006_0000(self, NwkId)
        self.log.logging("Heartbeat", "Debug", "++ pollingDeviceStatus -  %s  for ON/OFF" % (NwkId), NwkId)

    if len(getListOfEpForCluster(self, NwkId, "0008")) != 0:
        ReadAttributeRequest_0008_0000(self, NwkId)
        self.log.logging("Heartbeat", "Debug", "++ pollingDeviceStatus -  %s  for LVLControl" % (NwkId), NwkId)

    if len(getListOfEpForCluster(self, NwkId, "0102")) != 0:
        ReadAttributeRequest_0102_0008(self, NwkId)
        self.log.logging("Heartbeat", "Debug", "++ pollingDeviceStatus -  %s  for WindowCovering" % (NwkId), NwkId)

    if len(getListOfEpForCluster(self, NwkId, "0101")) != 0:
        ReadAttributeRequest_0101_0000(self, NwkId)
        self.log.logging("Heartbeat", "Debug", "++ pollingDeviceStatus -  %s  for DoorLock" % (NwkId), NwkId)

    if len(getListOfEpForCluster(self, NwkId, "0201")) != 0:
        ReadAttributeRequest_0201_0012(self, NwkId)
        self.log.logging("Heartbeat", "Debug", "++ pollingDeviceStatus -  %s  for Thermostat" % (NwkId), NwkId)
    return False


def checkHealth(self, NwkId):

    # Checking current state of the this Nwk
    if "Health" not in self.ListOfDevices[NwkId]:
        self.ListOfDevices[NwkId]["Health"] = ""

    if "Stamp" not in self.ListOfDevices[NwkId]:
        self.ListOfDevices[NwkId]["Stamp"] = {'LastPing': 0, 'LastSeen': 0}
        self.ListOfDevices[NwkId]["Health"] = "unknown"

    if "LastSeen" not in self.ListOfDevices[NwkId]["Stamp"]:
        self.ListOfDevices[NwkId]["Stamp"]["LastSeen"] = 0
        self.ListOfDevices[NwkId]["Health"] = "unknown"

    if (
        int(time.time())
        > (self.ListOfDevices[NwkId]["Stamp"]["LastSeen"] + 21200)
        and self.ListOfDevices[NwkId]["Health"] == "Live"
    ):
        if "ZDeviceName" in self.ListOfDevices[NwkId]:
            Domoticz.Error(
                "Device Health - %s Nwkid: %s,Ieee: %s , Model: %s seems to be out of the network"
                % (
                    self.ListOfDevices[NwkId]["ZDeviceName"],
                    NwkId,
                    self.ListOfDevices[NwkId]["IEEE"],
                    self.ListOfDevices[NwkId]["Model"],
                )
            )
        else:
            Domoticz.Error(
                "Device Health - Nwkid: %s,Ieee: %s , Model: %s seems to be out of the network"
                % (NwkId, self.ListOfDevices[NwkId]["IEEE"], self.ListOfDevices[NwkId]["Model"])
            )
        self.ListOfDevices[NwkId]["Health"] = "Not seen last 24hours"

    # If device flag as Not Reachable, don't do anything
    return (
        "Health" not in self.ListOfDevices[NwkId]
        or self.ListOfDevices[NwkId]["Health"] != "Not Reachable")


def pingRetryDueToBadHealth(self, NwkId):

    now = int(time.time())
    # device is on Non Reachable state
    self.log.logging("Heartbeat", "Debug", "--------> ping Retry Check %s" % NwkId, NwkId)
    if "pingDeviceRetry" not in self.ListOfDevices[NwkId]:
        self.ListOfDevices[NwkId]["pingDeviceRetry"] = {"Retry": 0, "TimeStamp": now}
    if self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"] == 0:
        return

    if "Retry" in self.ListOfDevices[NwkId]["pingDeviceRetry"] and "TimeStamp" not in self.ListOfDevices[NwkId]["pingDeviceRetry"]:
        # This could be due to a previous version without TimeStamp
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"] = 0
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["TimeStamp"] = now

    lastTimeStamp = self.ListOfDevices[NwkId]["pingDeviceRetry"]["TimeStamp"]
    retry = self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"]

    self.log.logging(
        "Heartbeat",
        "Debug",
        "--------> ping Retry Check %s Retry: %s Gap: %s" % (NwkId, retry, now - lastTimeStamp),
        NwkId,
    )
    # Retry #1
    if (
        retry == 0
        and self.ControllerLink.loadTransmit() == 0
        and now > (lastTimeStamp + 30)
    ):  # 30s
        self.log.logging("Heartbeat", "Debug", "--------> ping Retry 1 Check %s" % NwkId, NwkId)
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"] += 1
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["TimeStamp"] = now
        lookup_ieee = self.ListOfDevices[ NwkId ]['IEEE']
        zdp_NWK_address_request(self, "0000", lookup_ieee)
        submitPing(self, NwkId)
        return

    # Retry #2
    if (
        retry == 1
        and self.ControllerLink.loadTransmit() == 0
        and now > (lastTimeStamp + 120)
    ):  # 30 + 120s
        # Let's retry
        self.log.logging("Heartbeat", "Debug", "--------> ping Retry 2 Check %s" % NwkId, NwkId)
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"] += 1
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["TimeStamp"] = now
        lookup_ieee = self.ListOfDevices[ NwkId ]['IEEE']
        zdp_NWK_address_request(self, "FFFD", lookup_ieee)
        submitPing(self, NwkId)
        return

    # Retry #3
    if (
        retry == 2
        and self.ControllerLink.loadTransmit() == 0
        and now > (lastTimeStamp + 300)
    ):  # 30 + 120 + 300
        # Let's retry
        self.log.logging("Heartbeat", "Debug", "--------> ping Retry 3 (last) Check %s" % NwkId, NwkId)
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"] += 1
        self.ListOfDevices[NwkId]["pingDeviceRetry"]["TimeStamp"] = now
        lookup_ieee = self.ListOfDevices[ NwkId ]['IEEE']
        zdp_NWK_address_request(self, "FFFD", lookup_ieee)
        submitPing(self, NwkId)


def pingDevices(self, NwkId, health, checkHealthFlag, mainPowerFlag):

    if "pingDeviceRetry" in self.ListOfDevices[NwkId]:
        self.log.logging(
            "Heartbeat",
            "Debug",
            "------> pinDevices %s health: %s, checkHealth: %s, mainPower: %s, retry: %s"
            % (NwkId, health, checkHealthFlag, mainPowerFlag, self.ListOfDevices[NwkId]["pingDeviceRetry"]["Retry"]),
            NwkId,
        )
    else:
        self.log.logging(
            "Heartbeat",
            "Debug",
            "------> pinDevices %s health: %s, checkHealth: %s, mainPower: %s" % (NwkId, health, checkHealthFlag, mainPowerFlag),
            NwkId,
        )

    if not mainPowerFlag:
        return

    if (
        "Param" in self.ListOfDevices[NwkId]
        and "TuyaPing" in self.ListOfDevices[NwkId]["Param"]
        and int(self.ListOfDevices[NwkId]["Param"]["TuyaPing"]) == 1
    ):
        self.log.logging(
            "Heartbeat",
            "Debug",
            "------> pingDevice disabled for %s as TuyaPing enabled %s"
            % (
                NwkId,
                self.ListOfDevices[NwkId]["Param"]["TuyaPing"],
            ),
            NwkId,
        )
        return

    if (
        "Param" in self.ListOfDevices[NwkId]
        and "pingBlackListed" in self.ListOfDevices[NwkId]["Param"]
        and int(self.ListOfDevices[NwkId]["Param"]["pingBlackListed"]) == 1
    ):
        self.log.logging(
            "Heartbeat",
            "Debug",
            "------> pingDevice disabled for %s as pingBlackListed enabled %s"
            % (
                NwkId,
                self.ListOfDevices[NwkId]["Param"]["pingBlackListed"],
            ),
            NwkId,
        )
        return

    now = int(time.time())

    if (
        "time" in self.ListOfDevices[NwkId]["Stamp"]
        and now < self.ListOfDevices[NwkId]["Stamp"]["time"] + self.pluginconf.pluginConf["pingDevicesFeq"]
    ):
        # If we have received a message since less than 1 hours, then no ping to be done !
        self.log.logging("Heartbeat", "Debug", "------> %s no need to ping as we received a message recently " % (NwkId,), NwkId)
        return

    if not health:
        pingRetryDueToBadHealth(self, NwkId)
        return

    if "LastPing" not in self.ListOfDevices[NwkId]["Stamp"]:
        self.ListOfDevices[NwkId]["Stamp"]["LastPing"] = 0

    lastPing = self.ListOfDevices[NwkId]["Stamp"]["LastPing"]
    lastSeen = self.ListOfDevices[NwkId]["Stamp"]["LastSeen"]

    if checkHealthFlag and now > (lastPing + 60) and self.ControllerLink.loadTransmit() == 0:
        submitPing(self, NwkId)
        return

    self.log.logging(
        "Heartbeat",
        "Debug",
        "------> pinDevice %s time: %s LastPing: %s LastSeen: %s Freq: %s"
        % (NwkId, now, lastPing, lastSeen, self.pluginconf.pluginConf["pingDevicesFeq"]),
        NwkId,
    )

    if (
        (now > (lastPing + self.pluginconf.pluginConf["pingDevicesFeq"]))
        and (now > (lastSeen + self.pluginconf.pluginConf["pingDevicesFeq"]))
        and self.ControllerLink.loadTransmit() == 0
    ):

        self.log.logging(
            "Heartbeat",
            "Debug",
            "------> pinDevice %s time: %s LastPing: %s LastSeen: %s Freq: %s"
            % (NwkId, now, lastPing, lastSeen, self.pluginconf.pluginConf["pingDevicesFeq"]),
            NwkId,
        )

        submitPing(self, NwkId)


def submitPing(self, NwkId):
    # Pinging devices to check they are still Alive
    self.log.logging("Heartbeat", "Debug", "------------> call readAttributeRequest %s" % NwkId, NwkId)
    self.ListOfDevices[NwkId]["Stamp"]["LastPing"] = int(time.time())
    ping_device_with_read_attribute(self, NwkId)

def processKnownDevices(self, Devices, NWKID):
    # Begin
    # Normalize Hearbeat value if needed

    intHB = int(self.ListOfDevices[NWKID]["Heartbeat"])
    if intHB > 0xFFFF:
        intHB -= 0xFFF0
        self.ListOfDevices[NWKID]["Heartbeat"] = str(intHB)

    # Hack bad devices
    ReArrangeMacCapaBasedOnModel(self, NWKID, self.ListOfDevices[NWKID]["MacCapa"])

    # Check if this is a Main powered device or Not. Source of information are: MacCapa and PowerSource
    _mainPowered = mainPoweredDevice(self, NWKID)
    _checkHealth = self.ListOfDevices[NWKID]["Health"] == ""
    health = checkHealth(self, NWKID)

    # Pinging devices to check they are still Alive
    if self.pluginconf.pluginConf["pingDevices"]:
        pingDevices(self, NWKID, health, _checkHealth, _mainPowered)

    # Check if we are in the process of provisioning a new device. If so, just stop
    if self.CommiSSionning:
        return

    # If device flag as Not Reachable, don't do anything
    if not health:
        self.log.logging(
            "Heartbeat",
            "Debug",
            "processKnownDevices -  %s stop here due to Health %s" % (NWKID, self.ListOfDevices[NWKID]["Health"]),
            NWKID,
        )
        return

    # If we reach this step, the device health is Live
    if "pingDeviceRetry" in self.ListOfDevices[NWKID]:
        self.log.logging("Heartbeat", "Log", "processKnownDevices -  %s recover from Non Reachable" % NWKID, NWKID)
        del self.ListOfDevices[NWKID]["pingDeviceRetry"]

    model = self.ListOfDevices[NWKID]["Model"] if "Model" in self.ListOfDevices[NWKID] else ""
    enabledEndDevicePolling = bool(model in self.DeviceConf and "PollingEnabled" in self.DeviceConf[model] and self.DeviceConf[model]["PollingEnabled"])

    if "CheckParam" in self.ListOfDevices[NWKID] and self.ListOfDevices[NWKID]["CheckParam"] and intHB > (60 // HEARTBEAT):
        sanity_check_of_param(self, NWKID)
        self.ListOfDevices[NWKID]["CheckParam"] = False

    if ( intHB % CHECKING_DELAY_READATTRIBUTE) == 0:
        check_delay_readattributes( self, NWKID )

    if ( 
        "DelayBindingAtPairing" in self.ListOfDevices[ NWKID ] 
        and isinstance(self.ListOfDevices[ NWKID ]["DelayBindingAtPairing"],int )
        and self.ListOfDevices[ NWKID ]["DelayBindingAtPairing"] > 0
        and time.time() > self.ListOfDevices[ NWKID ]["DelayBindingAtPairing"]
    ):   
        # Will check only after a Command has been sent, in order to limit.
        self.log.logging("Heartbeat", "Log", "check_delay_binding inHB = %s" %intHB ) 
        check_delay_binding( self, NWKID, model )

    # Starting this point, it is ony relevant for Main Powered Devices.
    # Some battery based end device with ZigBee 30 use polling and can receive commands.
    # We should authporized them for Polling After Action, in order to get confirmation.
    if not _mainPowered and not enabledEndDevicePolling:
        return

    # Action not taken, must be reschedule to next cycle
    rescheduleAction = False

    if self.pluginconf.pluginConf["forcePollingAfterAction"] and (intHB == 1):  # HB has been reset to 0 as for a Group command
        # intHB is 1 as if it has been reset, we get +1 in ProcessListOfDevices
        self.log.logging("Heartbeat", "Debug", "processKnownDevices -  %s due to intHB %s" % (NWKID, intHB), NWKID)
        rescheduleAction = rescheduleAction or pollingDeviceStatus(self, NWKID)
        # Priority on getting the status, nothing more to be done!
        return

    # Polling Manufacturer Specific devices ( Philips, Gledopto  ) if applicable
    rescheduleAction = rescheduleAction or pollingManufSpecificDevices(self, NWKID, intHB)

    _doReadAttribute = bool((self.pluginconf.pluginConf["enableReadAttributes"] or self.pluginconf.pluginConf["resetReadAttributes"]) and intHB != 0 and (intHB % READATTRIBUTE_FEQ) == 0)

    if ( 
        self.ControllerLink.loadTransmit() > 5
        and 'PairingTime' in self.ListOfDevices[ NWKID ]
        and time.time() <= ( self.ListOfDevices[ NWKID ]["PairingTime"] + ( self.ControllerLink.loadTransmit() // 5 ) + 15 ) 
        ):
        # In case we have just finished the pairing give 3 minutes to finish.
        self.log.logging(
            "Heartbeat",
            "Debug",
            "processKnownDevices -  %s delay the next ReadAttribute to closed to the pairing %s" % (NWKID, self.ListOfDevices[ NWKID ]["PairingTime"],),
            NWKID,
        )
        return
            
    if _doReadAttribute:
        self.log.logging(
            "Heartbeat",
            "Log",
            "processKnownDevices -  %s intHB: %s _mainPowered: %s doReadAttr: %s" % (NWKID, intHB, _mainPowered, _doReadAttribute),
            NWKID,
        )

        # Read Attributes if enabled
        now = int(time.time())  # Will be used to trigger ReadAttributes
        for tmpEp in self.ListOfDevices[NWKID]["Ep"]:
            if tmpEp == "ClusterType":
                continue

            for Cluster in READ_ATTRIBUTES_REQUEST:
                if Cluster in ("Type", "ClusterType", "ColorMode"):
                    continue
                if Cluster not in self.ListOfDevices[NWKID]["Ep"][tmpEp]:
                    continue

                if "Model" in self.ListOfDevices[NWKID]:
                    if (
                        self.ListOfDevices[NWKID]["Model"] == "lumi.ctrl_neutral1" and tmpEp != "02"
                    ):  # All Eps other than '02' are blacklisted
                        continue
                    if self.ListOfDevices[NWKID]["Model"] == "lumi.ctrl_neutral2" and tmpEp not in ("02", "03"):
                        continue

                if self.busy or self.ControllerLink.loadTransmit() > MAX_LOAD_ZIGATE:
                    self.log.logging(
                        "Heartbeat",
                        "Debug",
                        "--  -  %s skip ReadAttribute for now ... system too busy (%s/%s)"
                        % (NWKID, self.busy, self.ControllerLink.loadTransmit()),
                        NWKID,
                    )
                    rescheduleAction = True
                    continue  # Do not break, so we can keep all clusters on the same states

                func = READ_ATTRIBUTES_REQUEST[Cluster][0]
                # For now it is a hack, but later we might put all parameters
                if READ_ATTRIBUTES_REQUEST[Cluster][1] in self.pluginconf.pluginConf:
                    timing = self.pluginconf.pluginConf[READ_ATTRIBUTES_REQUEST[Cluster][1]]
                else:
                    Domoticz.Error(
                        "processKnownDevices - missing timing attribute for Cluster: %s - %s"
                        % (Cluster, READ_ATTRIBUTES_REQUEST[Cluster][1])
                    )
                    continue

                # Let's check the timing
                if not is_time_to_perform_work(self, "ReadAttributes", NWKID, tmpEp, Cluster, now, timing):
                    continue

                self.log.logging(
                    "Heartbeat",
                    "Debug",
                    "-- -  %s/%s and time to request ReadAttribute for %s" % (NWKID, tmpEp, Cluster),
                    NWKID,
                )

                func(self, NWKID)

    # Call Schneider Reenforcement if needed
    if self.pluginconf.pluginConf["reenforcementWiser"] and (self.HeartbeatCount % self.pluginconf.pluginConf["reenforcementWiser"]) == 0:
        rescheduleAction = rescheduleAction or schneiderRenforceent(self, NWKID)

    if self.pluginconf.pluginConf["checkConfigurationReporting"]:
        rescheduleAction = rescheduleAction or check_configuration_reporting(self, NWKID, _mainPowered, intHB)

    if night_shift_jobs( self ) and _mainPowered and not enabledEndDevicePolling and intHB != 0 and ((intHB % ATTRIBUTE_DISCOVERY_REFRESH) == 0):
        rescheduleAction = rescheduleAction or attributeDiscovery(self, NWKID)
        
    if night_shift_jobs( self ) and _mainPowered and not enabledEndDevicePolling and intHB != 0 and ((intHB % BINDING_TABLE_REFRESH) == 0):
        mgtm_binding(self, NWKID, "BindingTable")

    # If corresponding Attributes not present, let's do a Request Node Description
    if night_shift_jobs( self ) and not enabledEndDevicePolling and intHB != 0 and ((intHB % NODE_DESCRIPTOR_REFRESH) == 0):
        req_node_descriptor = False
        if (
            "Manufacturer" not in self.ListOfDevices[NWKID]
            or "DeviceType" not in self.ListOfDevices[NWKID]
            or "LogicalType" not in self.ListOfDevices[NWKID]
            or "PowerSource" not in self.ListOfDevices[NWKID]
            or "ReceiveOnIdle" not in self.ListOfDevices[NWKID]
        ):
            req_node_descriptor = True
        if (
            "Manufacturer" in self.ListOfDevices[NWKID]
            and self.ListOfDevices[NWKID]["Manufacturer"] == ""
        ):
            req_node_descriptor = True

        if ( req_node_descriptor and night_shift_jobs( self ) ):
            
            if not self.busy and self.ControllerLink.loadTransmit() <= MAX_LOAD_ZIGATE:
                #sendZigateCmd(self, "0042", str(NWKID), ackIsDisabled=True)  # Request a Node Descriptor
                zdp_node_descriptor_request(self, NWKID)
            else:
                rescheduleAction = True

    if rescheduleAction and intHB != 0:  # Reschedule is set because Zigate was busy or Queue was too long to process
        self.ListOfDevices[NWKID]["Heartbeat"] = str(intHB - 1)  # So next round it trigger again

    return

def check_configuration_reporting(self, NWKID, _mainPowered, intHB):
    
    self.log.logging( "ConfigureReporting", "Debug", "check_configuration_reporting for %s %s %s %s %s >%s<" %(
        NWKID, _mainPowered, self.HeartbeatCount, intHB, self.pluginconf.pluginConf["checkConfigurationReporting"], self.zigbee_communication), NWKID)

    if self.configureReporting is None:
        # Cfg Reporting Object not yet ready
        return

    if self.HeartbeatCount < QUIET_AFTER_START:
        #  leave time at startup
        return

    if "Status" not in self.ListOfDevices[NWKID] or self.ListOfDevices[NWKID]["Status"] != "inDB":
        # Device is not a good state
        return False

    if intHB != 0 and (intHB % (60 // HEARTBEAT)) != 0:
        # check only every minute
        return

    if (
        "checkConfigurationReporting" not in self.pluginconf.pluginConf
        or self.pluginconf.pluginConf["checkConfigurationReporting"] == 0
    ):
        # Check if checkConfigurationReporting is enable
        return

    if deviceconf_device(self, NWKID) == {}:
        # Do only for plugin known devices
        return

    if not _mainPowered:
        # Process only with main powered devices
        return

    if not night_shift_jobs( self ):
        # In case we are in a night shift mode, then wait for the nigh window
        return

    if self.busy and self.ControllerLink.loadTransmit() > 3:
        # Only if the load is reasonable
        return True


    if self.zigbee_communication == "zigpy":
        self.log.logging( "ConfigureReporting", "Debug", "check_configuration_reporting for %s %s %s %s %s >%s<" %(
            NWKID, _mainPowered, self.HeartbeatCount, intHB, self.pluginconf.pluginConf["checkConfigurationReporting"], self.zigbee_communication), NWKID)

        if ( not self.configureReporting.check_configuration_reporting_for_device( NWKID, checking_period=self.pluginconf.pluginConf["checkConfigurationReporting"] )):
            # Nothing trigger, let's check if the configure reporting are correct
            self.configureReporting.check_and_redo_configure_reporting_if_needed( NWKID)

    elif self.zigbee_communication == "native":
        self.log.logging( "ConfigureReporting", "Debug", "Trying Configuration reporting for %s/%s !" %(
            NWKID, get_device_nickname( self, NwkId=NWKID)), NWKID)
        self.configureReporting.processConfigureReporting( NWKID, batch=True )
    return False


def processListOfDevices(self, Devices):
    # Let's check if we do not have a command in TimeOut

    # self.ControllerLink.checkTOwaitFor()
    entriesToBeRemoved = []

    for NWKID in list(self.ListOfDevices.keys()):
        if NWKID in ("ffff", "0000"):
            continue

        # If this entry is empty, then let's remove it .
        if len(self.ListOfDevices[NWKID]) == 0:
            self.log.logging("Heartbeat", "Debug", "Bad devices detected (empty one), remove it, adr:" + str(NWKID), NWKID)
            entriesToBeRemoved.append(NWKID)
            continue

        status = self.ListOfDevices[NWKID]["Status"]
        if self.ListOfDevices[NWKID]["RIA"] not in ( "", {}):
            RIA = int(self.ListOfDevices[NWKID]["RIA"])
        else:
            RIA = 0
            self.ListOfDevices[NWKID]["RIA"] = "0"

        self.ListOfDevices[NWKID]["Heartbeat"] = str(int(self.ListOfDevices[NWKID]["Heartbeat"]) + 1)

        if status == "failDB":
            entriesToBeRemoved.append(NWKID)
            continue

        # Known Devices
        if status == "inDB":
            processKnownDevices(self, Devices, NWKID)

        elif status == "Leave":
            timedOutDevice(self, Devices, NwkId=NWKID)
            # Device has sentt a 0x8048 message annoucing its departure (Leave)
            # Most likely we should receive a 0x004d, where the device come back with a new short address
            # For now we will display a message in the log every 1'
            # We might have to remove this entry if the device get not reconnected.
            if ((int(self.ListOfDevices[NWKID]["Heartbeat"]) % 36) and int(self.ListOfDevices[NWKID]["Heartbeat"]) != 0) == 0:
                if "ZDeviceName" in self.ListOfDevices[NWKID]:
                    self.log.logging(
                        "Heartbeat",
                        "Debug",
                        "processListOfDevices - Device: %s (%s) is in Status = 'Left' for %s HB"
                        % (self.ListOfDevices[NWKID]["ZDeviceName"], NWKID, self.ListOfDevices[NWKID]["Heartbeat"]),
                        NWKID,
                    )
                else:
                    self.log.logging(
                        "Heartbeat",
                        "Debug",
                        "processListOfDevices - Device: (%s) is in Status = 'Left' for %s HB"
                        % (NWKID, self.ListOfDevices[NWKID]["Heartbeat"]),
                        NWKID,
                    )
                # Let's check if the device still exist in Domoticz
                for Unit in Devices:
                    if self.ListOfDevices[NWKID]["IEEE"] == Devices[Unit].DeviceID:
                        self.log.logging(
                            "Heartbeat",
                            "Debug",
                            "processListOfDevices - %s  is still connected cannot remove. NwkId: %s IEEE: %s "
                            % (Devices[Unit].Name, NWKID, self.ListOfDevices[NWKID]["IEEE"]),
                            NWKID,
                        )
                        fnd = True
                        break
                else:  # We browse the all Devices and didn't find any IEEE.
                    if "IEEE" in self.ListOfDevices[NWKID]:
                        Domoticz.Log(
                            "processListOfDevices - No corresponding device in Domoticz for %s/%s"
                            % (NWKID, str(self.ListOfDevices[NWKID]["IEEE"]))
                        )
                    else:
                        Domoticz.Log("processListOfDevices - No corresponding device in Domoticz for %s" % (NWKID))
                    fnd = False

                if not fnd:
                    # Not devices found in Domoticz, so we are safe to remove it from Plugin
                    if self.ListOfDevices[NWKID]["IEEE"] in self.IEEE2NWK:
                        Domoticz.Status(
                            "processListOfDevices - Removing %s / %s from IEEE2NWK." % (self.ListOfDevices[NWKID]["IEEE"], NWKID)
                        )
                        del self.IEEE2NWK[self.ListOfDevices[NWKID]["IEEE"]]
                    Domoticz.Status("processListOfDevices - Removing the entry %s from ListOfDevice" % (NWKID))
                    removeNwkInList(self, NWKID)

        elif status not in ("inDB", "UNKNOW", "erasePDM"):
            # Discovery process 0x004d -> 0x0042 -> 0x8042 -> 0w0045 -> 0x8045 -> 0x0043 -> 0x8043
            processNotinDBDevices(self, Devices, NWKID, status, RIA)
    # end for key in ListOfDevices

    for iterDevToBeRemoved in entriesToBeRemoved:
        if "IEEE" in self.ListOfDevices[iterDevToBeRemoved]:
            del self.ListOfDevices[iterDevToBeRemoved]["IEEE"]
        del self.ListOfDevices[iterDevToBeRemoved]

    if self.CommiSSionning or self.busy:
        self.log.logging(
            "Heartbeat",
            "Debug",
            "Skip LQI, ConfigureReporting and Networkscan du to Busy state: Busy: %s, Enroll: %s" % (self.busy, self.CommiSSionning),
        )
        return  # We don't go further as we are Commissioning a new object and give the prioirty to it

    # Network Topology
    if self.networkmap:
        phase = self.networkmap.NetworkMapPhase()
        self.log.logging("Heartbeat", "Debug", "processListOfDevices checking Topology phase: %s" % phase)
        # if phase == 0:
        #    self.networkmap.start_scan( )
        if phase == 1:
            self.log.logging("Heartbeat", "Status", "Starting Network Topology")
            self.networkmap.start_scan()
        elif phase == 2:
            self.log.logging(
                "Heartbeat",
                "Debug",
                "processListOfDevices Topology scan is possible %s" % self.ControllerLink.loadTransmit(),
            )
            if self.ControllerLink.loadTransmit() < MAX_LOAD_ZIGATE:
                self.networkmap.continue_scan()

    # if (self.HeartbeatCount > QUIET_AFTER_START) and (self.HeartbeatCount > NETWORK_ENRG_START):
    #    # Network Energy Level
    if self.networkenergy and self.ControllerLink.loadTransmit() <= MAX_LOAD_ZIGATE:
        self.networkenergy.do_scan()

    self.log.logging(
        "Heartbeat",
        "Debug",
        "processListOfDevices END with HB: %s, Busy: %s, Enroll: %s, Load: %s"
        % (self.HeartbeatCount, self.busy, self.CommiSSionning, self.ControllerLink.loadTransmit()),
    )
    return
