#!/usr/bin/env python3
"""Preview the actual plugin with installed Omarchy components, outside the desktop shell."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

SMOKE = r'''
  property int languageStep: 0
  property int step: 0
  property int ticks: 0
  property bool capturing: false
  property bool exercisedDismissal: false
  property int memorySetupStep: 0
  property int launcherSetupStep: 0
  property int backupSetupStep: 0
  property int memoryFlowStep: 0
  property int updateSetupStep: 0
  property int agentSetupStep: 0
  property int localToolStep: 0
  property int piSetupStep: Quickshell.env("AI_HARNESS_PI_SMOKE") === "1" ? 0 : 20
  property int navigationStep: 0
  property int briefStep: 0
  property int panelClosures: 0
  property int messageClosures: 0
  Connections {
    target: harness
    function onOpenedChanged() { if (!harness.opened) panelClosures++ }
  }
  property bool overviewCaptured: false
  property bool piPaidCaptured: false
  property bool tokensAdvancedChecked: false
  property int scopePopupStep: 0
  property int teamStep: 0
  property real teamRowY: 0
  property real teamContentHeight: 0
  property real teamContentY: 0
  property int selectorStep: 0
  property real selectorContentHeight: 0
  property int teamWaits: 0
  // keyClick spins the event loop: without this guard the timer re-enters mid-keypress and skips a step.
  property bool teamKeying: false
  property bool teamHoverMoved: false
  property bool teamHoverChecked: false
  property int teamModelCommands: 0
  property bool piAdvancedChecked: false
  property bool longStatusChecked: false
  property int selectionCommands: 0
  property int missingSelectionCommands: 0
  property int messageStep: 0
  property int zeroTouchStep: 0
  property int persistWaits: 0
  property int zeroTouchWaits: 0
  property bool zeroTouchExpectError: false
  property string zeroTouchRepairedAt: ""
  property double firstMessageAt: 0
  property double secondMessageAt: 0
  property string secondMessage: ""
  property string expectedError: ""

  Process {
    id: prepareSecondProfile
    command: ["mkdir", "-p", Quickshell.env("HOME") + "/.codex-2"]
  }

  function find(object, name, seen) {
    if (!object || seen.indexOf(object) !== -1) return null
    seen.push(object)
    if (object.objectName === name) return object
    var children = object.data || object.children || []
    for (var i = 0; i < children.length; i++) {
      var result = find(children[i], name, seen)
      if (result) return result
    }
    if (object.contentItem) return find(object.contentItem, name, seen)
    return null
  }

  function fail(message) {
    console.error("HARNESS_SMOKE_FAIL: " + message)
    Qt.quit()
  }

  // The panel window covers the screen, so popups float past the card without moving it.
  function popupFitsWindow(control) {
    var window = control.Window.window
    var tallest = 8 * Style.spacing.popupRowHeight + 7 * Style.spacing.labelGap + Style.spacing.xxs
    return !!window && control.mapToItem(null,0,control.height).y + tallest <= window.height
  }

  function shot(name, noAdvance) {
    capturing = true
    var content = find(harness, "harnessContent", [])
    if (!content) { fail("Content not found"); return }
    var surface = content.parent.parent
    if (!surface || typeof surface.grabToImage !== "function") { fail("Native popup surface not found"); return }
    if (!surface.Window.window) { capturing = false; return }
    if (!surface.grabToImage(function(result) {
      if (!result.saveToFile(Quickshell.env("AI_HARNESS_SCREENSHOTS") + "/" + name + ".png"))
        fail("Screenshot failed: " + name)
      capturing = false
      if (!noAdvance) step++
    })) fail("Could not capture: " + name)
  }

  // Window selection on a real HarnessContent with synthetic snapshots: the figures never mix windows and the choice is immediate.
  property bool windowProbeDone: false
  property var windowRequests: []
  function readyUsage(days) {
    return {since: "2026-10-26T00:00:00+00:00", asOf: "2026-11-02T00:00:00+00:00", windowDays: days, partialWindow: false,
            classificationLimited: days === 7, pending: false, total: days + "00 tokens",
            roles: [{role: "chief", name: "Jefe", share: 1, tokens: days + "00 tokens"}]}
  }
  function probeSnapshot(usage) { return {agents: [], components: [], deferred: [], roleUsage: usage} }
  function probeVisible(name) { var item = find(windowProbe, name, []); return !!item && item.visible }
  function probeButton(days) {
    var group = find(windowProbe, "roles-window", [])
    var buttons = group ? group.children : []
    for (var i = 0; i < buttons.length; i++) if (buttons[i].text === days + " días") return buttons[i]
    return null
  }
  function probeCheck(label, condition) { if (!condition) { fail("Window selection: " + label); return false } return true }
  function probeState(label, days, figures) {
    var group = find(windowProbe, "roles-window", [])
    var button = probeButton(days)
    return probeCheck(label + ": value", !!group && group.value === String(days)) && !!button && probeCheck(label + ": selected", button.selected)
      && probeCheck(label + ": total", probeVisible("roles-total") === figures) && probeCheck(label + ": rows", probeVisible("roles-row") === figures)
      && probeCheck(label + ": since", probeVisible("roles-since") === figures) && probeCheck(label + ": asof", probeVisible("roles-asof") === figures)
      && probeCheck(label + ": updating", probeVisible("roles-updating") === !figures)
  }
  function probeClick(days, expected) {
    var button = probeButton(days)
    if (!probeCheck("button " + days, !!button && button.enabled)) return false
    button.clicked()
    return probeCheck("request " + days, windowRequests.length === expected && windowRequests[expected - 1].join() === "usage-window," + days)
  }
  function runWindowProbe() {
    windowProbe.busy = false
    windowProbe.snapshot = probeSnapshot(readyUsage(7))
    windowProbe.page = "roles"
    if (!probeState("ready 7", 7, true) || !probeCheck("limited note", probeVisible("roles-overlap"))) return
    // 7 -> 30: the choice shows before any answer or busy state, hides the old figures and emits one command.
    if (!probeClick(30, 1) || !probeState("asked 30", 30, false)) return
    windowProbe.page = ""
    var row = find(windowProbe, "usage-total", [])
    if (!probeCheck("main total", !!row && row.status === "")) return
    windowProbe.page = "roles"
    windowProbe.busy = true
    if (!probeCheck("selector locked", !probeButton(7).enabled && !probeButton(30).enabled)) return
    windowProbe.snapshot = probeSnapshot(readyUsage(7))
    windowProbe.busy = false
    if (!probeState("stale 7", 30, false)) return
    var late = readyUsage(30); late.pending = true
    windowProbe.snapshot = probeSnapshot(late)
    if (!probeState("pending 30", 30, false)) return
    windowProbe.snapshot = probeSnapshot(readyUsage(30))
    if (!probeState("ready 30", 30, true) || !probeCheck("limited note hidden", !probeVisible("roles-overlap"))) return
    // 30 -> 7 and back to the answer.
    if (!probeClick(7, 2) || !probeState("asked 7", 7, false)) return
    windowProbe.snapshot = probeSnapshot(readyUsage(7))
    if (!probeState("ready 7 again", 7, true)) return
    // A failed request keeps the choice, hides the figures and can be repeated.
    if (!probeClick(30, 3)) return
    windowProbe.busy = true
    windowProbe.errorText = "synthetic failure"
    windowProbe.busy = false
    if (!probeState("failed 30", 30, false) || !probeCheck("selector unlocked", probeButton(30).enabled)) return
    if (!probeClick(30, 4)) return
    windowProbe.errorText = ""
    windowProbe.snapshot = probeSnapshot(readyUsage(30))
    if (!probeState("retry 30", 30, true)) return
    windowProbeDone = true
    probeWindow.visible = false
  }
  PanelWindow {
    id: probeWindow
    implicitWidth: 380
    implicitHeight: 640
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    anchors { top: true; left: true }
    Harness.HarnessContent {
      id: windowProbe
      anchors.fill: parent
      onCommandRequested: args => { var seen = windowRequests.slice(); seen.push(args); windowRequests = seen }
    }
  }
  Timer {
    interval: 250
    running: !windowProbeDone && probeWindow.visible && windowProbe.width > 0
    repeat: false
    onTriggered: runWindowProbe()
  }

  Timer {
    interval: 250
    running: true
    repeat: true
    onTriggered: {
      if (++ticks > 520) { fail("Timeout"); return }
      if (Quickshell.env("AI_HARNESS_WINDOW_SMOKE") === "1") {  // only the window-selection probe
        if (windowProbeDone) { console.log("HARNESS_SMOKE_PASS"); Qt.quit() }
        return
      }
      if (harness.errorText && messageStep !== 6 && messageStep !== 7 && !zeroTouchExpectError) { fail(harness.errorText); return }
      if (step < 15 && !harness.opened && !(step === 14 && zeroTouchStep < 13)) { harness.open(); return }
      if (harness.busy || capturing || !harness.snapshot.agents.length) return
      var agent = null
      for (var i = 0; i < harness.snapshot.agents.length; i++) {
        if (harness.snapshot.agents[i].id === "codex") agent = harness.snapshot.agents[i]
      }
      if (!agent || agent.installation !== "installed") { fail("Codex not detected"); return }
      var selector = find(harness, "agent-selector", [])
      if (!selector) { fail("Pointer target unavailable: agent-selector"); return }
      var second = harness.snapshot.agents.find(a => a.id === "codex-2")
      var component = harness.snapshot.components.find(c => c.id === "superpowers")
      var componentToggle = find(harness, "goal-quality", [])
      if (!second || !component || !componentToggle) { fail("Shared component or second profile missing"); return }

      if (selectorStep < 2) {
        var selectorContent = find(harness,"harnessContent",[])
        if (selectorStep === 0) {
          // Busy while the status check that runs when the plugin loads is still going.
          if (!selector.enabled) return
          selectorContentHeight = selectorContent.implicitHeight
          selector.open();selectorStep++;return
        }
        if (!selector.popupOpen) { selector.open();return }
        if (selectorContent.implicitHeight !== selectorContentHeight) { fail("Opening the agent selector stretched the panel"); return }
        selector.close();selectorStep++;return
      }

      if (languageStep < 3) {
        harness.page = "settings"
        var languageSelector = find(harness, "language-selector", [])
        var languageLabel = find(harness, "language-label", [])
        if (!languageSelector || !languageLabel || !languageLabel.visible) { fail("Language settings missing"); return }
        if (languageStep === 0) {
          if (harness.snapshot.language !== "es" || languageLabel.text !== "Idioma") { fail("Spanish label missing"); return }
          languageSelector.changed("en")
        } else if (languageStep === 1) {
          if (harness.snapshot.language !== "en" || languageLabel.text !== "Language") { fail("English label did not update immediately"); return }
          languageSelector.changed("es")
        } else {
          if (harness.snapshot.language !== "es" || languageLabel.text !== "Idioma") { fail("Spanish label did not return immediately"); return }
          harness.page = ""
        }
        languageStep++; return
      }

      if (navigationStep < 28) {
        var click = function(name, button) {
          var item=find(harness,name,[])
          if (!item || !item.visible || !item.enabled) { fail("Pointer target unavailable: " + name); return }
          pointer.mouseClick(item,item.width / 2,item.height / 2,button || Qt.LeftButton)
        }
        if (navigationStep === 0) {
          if (harness.page !== "" || find(harness,"tabs",[])) { fail("Main view must have no tabs"); return }
          var names=["summary","goal-quality","goal-memory","goal-tokens","goal-security","launch-default","update-all","open-settings"]
          for (var n=0;n<names.length;n++) {
            var mainItem=find(harness,names[n],[])
            if (!mainItem || !mainItem.visible) { fail("Main target unavailable: " + names[n]); return }
          }
          var panel=find(harness,"harnessPanel",[])
          var content=find(harness,"harnessContent",[])
          if (panel.contentWidth > Style.space(380)) { fail("Panel is wider than audio"); return }
          if (!longStatusChecked) {
            find(harness,"harnessContent",[]).commandRequested.connect(function(args) {
              if (args[0] === "set-agents") selectionCommands++
            })
            var longSnapshot=Object.assign({},harness.snapshot)
            longSnapshot.goals=harness.snapshot.goals.map(function(goal) {
              return Object.assign({},goal,{line:"rg, jq listos · ast-grep falta · estado largo para comprobar el recorte"})
            })
            harness.snapshot=longSnapshot
            longStatusChecked=true
            return
          }
          for (var g=0;g<harness.snapshot.goals.length;g++) {
            var row=find(harness,"goal-"+harness.snapshot.goals[g].id,[])
            var control=find(harness,row.objectName+"-switch",[])
            if (row.width > content.width || control.mapToItem(content,control.width,0).x > content.width) { fail("Long status pushed the goal switch outside content"); return }
          }
          if (!overviewCaptured) { overviewCaptured=true;shot("main",true);return }
          selector.changed(["codex"])
        } else if (navigationStep === 1) {
          if (!agent.enabled || harness.page !== "") { fail("Selector did not select independently"); return }
          selector.changed([])
        } else if (navigationStep === 2) {
          if (agent.enabled || harness.page !== "") { fail("Selector did not deselect independently"); return }
          // claude-2 has no CLI in the fixture (its launcher is a stub), so it cannot be created by selecting it.
          // codex-2 may be selected: its CLI is present and selecting it creates the empty profile.
          var missing = harness.snapshot.agents.find(a => a.id === "claude-2")
          if (!missing || missing.installation !== "missing-profile" || missing.cliInstalled || missing.enabled) { fail("Profile without CLI must initially be missing and unselected"); return }
          missingSelectionCommands=selectionCommands
          // Match the native widget, which writes values before emitting changed.
          selector.values=["claude-2"]
          selector.changed(["claude-2"])
        } else if (navigationStep === 3) {
          if (selectionCommands !== missingSelectionCommands || harness.snapshot.agents.find(a => a.id === "claude-2").enabled || selector.values.length || harness.page !== "") { fail("Missing profile was submitted or kept selected"); return }
          click("open-settings")
        } else if (navigationStep === 4) {
          if (harness.page !== "settings") { fail("Settings did not open"); return }
          click("agent-row-codex")
        } else if (navigationStep === 5) {
          if (harness.page !== "codex" || agent.enabled || !find(harness,"update-agent-codex",[]).visible) { fail("Agent settings changed selection or failed to open"); return }
          // The mouse back button behaves like ‹ Volver.
          var backContent=find(harness,"harnessContent",[])
          pointer.mouseClick(backContent,backContent.width / 2,backContent.height / 2,Qt.BackButton)
        } else if (navigationStep === 6) {
          if (harness.page !== "settings") { fail("Mouse back button did not return to settings"); return }
          click("navigation-back")
        } else if (navigationStep === 7) {
          if (harness.page !== "") { fail("Settings back did not return home"); return }
          click("goal-memory-switch")
        } else if (navigationStep === 8) {
          if (!harness.snapshot.components.find(c => c.id === "memory").enabled || harness.page !== "") { fail("Memory switch did not enable independently"); return }
          click("goal-memory-switch")
        } else if (navigationStep === 9) {
          if (harness.snapshot.components.find(c => c.id === "memory").enabled || harness.page !== "") { fail("Memory switch did not disable independently"); return }
          click("goal-memory-open")
        } else if (navigationStep === 10) {
          if (harness.page !== "memory" || !find(harness,"memory-backup-destination",[]).visible || !find(harness,"memory-export",[]).visible || find(harness,"ecc-pages",[])) { fail("Memory settings and recovery must share one page"); return }
          click("navigation-back")
        } else if (navigationStep === 11) {
          if (harness.page !== "") { fail("Memory back did not return home"); return }
          click("goal-quality-open")
        } else if (navigationStep === 12) {
          var qualityUpdate=find(harness,"quality-update",[])
          if (harness.page !== "quality" || !qualityUpdate || qualityUpdate.visible !== component.targets.some(target => target.installed)) { fail("Quality page or applicable update action missing"); return }
          click("navigation-back")
        } else if (navigationStep === 13) {
          if (harness.page !== "") { fail("Quality back did not return home"); return }
          click("goal-tokens-open")
        } else if (navigationStep === 14) {
          if (briefStep === 0) {
            if (find(harness,"tool-brief-rg",[]).visible) { fail("Tool brief must start collapsed"); return }
            click("tool-rg-open"); briefStep=1; return
          }
          if (briefStep === 1) {
            var brief=find(harness,"tool-brief-rg",[])
            if (!brief.visible || !brief.text) { fail("Tool brief did not expand"); return }
            click("tool-jq-open"); briefStep=2; return
          }
          if (briefStep === 2) {
            if (find(harness,"tool-brief-rg",[]).visible || !find(harness,"tool-brief-jq",[]).visible) { fail("Tool accordion did not switch to jq"); return }
            click("tool-jq-open"); briefStep=3; return
          }
          if (briefStep === 3) {
            if (find(harness,"tool-brief-jq",[]).visible) { fail("Tool brief did not collapse"); return }
            briefStep=4
          }
          if (!tokensAdvancedChecked) {
            click("advanced-tokens")
            tokensAdvancedChecked=true
            return
          }
          if (harness.page !== "tokens" || !find(harness,"local-tools-scope",[]).visible || !find(harness,"local-tool-rg",[]).visible || find(harness,"local-tool-git",[]) || find(harness,"local-tool-shellcheck",[]) || find(harness,"local-tools-search",[]) || find(harness,"local-tools-category",[])) { fail("Tokens page not filtered or advanced controls missing"); return }
          var scopeControl=find(harness,"local-tools-scope",[])
          var scopeScroll=find(harness,"harness-scroll",[])
          if (scopeControl.options.length <= 8) { fail("Scope fixture must exercise a long list"); return }
          if (scopePopupStep === 0) {
            // Put the trigger at the lower edge before opening the long list.
            scopeScroll.contentItem.contentY=Math.max(0,scopeControl.mapToItem(scopeScroll.contentItem.contentItem,0,scopeControl.height).y-scopeScroll.height)
            scopePopupStep++;return
          }
          if (scopePopupStep === 1) { scopeControl.open();scopePopupStep++;return }
          if (scopePopupStep === 2) {
            if (!scopeControl.popupOpen || !popupFitsWindow(scopeControl)) { fail("Scope popup extends beyond window"); return }
            scopeControl.close();scopePopupStep++;return
          }
          if (scopePopupStep === 3) {
            if (scopeControl.popupOpen) { fail("Scope popup did not close"); return }
            scopeScroll.contentItem.contentY=0
            scopePopupStep++;return
          }
          // A second tap on an open list must close it, not reopen it.
          if (scopePopupStep === 4) { pointer.mouseClick(scopeControl,scopeControl.width / 2,scopeControl.height / 2,Qt.LeftButton);scopePopupStep++;return }
          if (scopePopupStep === 5) {
            if (!scopeControl.popupOpen) { fail("Scope list did not open on tap"); return }
            pointer.mouseClick(scopeControl,scopeControl.width / 2,scopeControl.height / 2,Qt.LeftButton);scopePopupStep++;return
          }
          if (scopePopupStep === 6) {
            if (scopeControl.popupOpen) { fail("Second tap reopened the list instead of closing it"); return }
            scopePopupStep++;return
          }
          click("navigation-back")
        } else if (navigationStep === 15) {
          if (harness.page !== "") { fail("Tokens back did not return home"); return }
          click("goal-security-open")
        } else if (navigationStep === 16) {
          if (harness.page !== "security" || !find(harness,"advanced-security",[]).visible || find(harness,"local-tool-rg",[]) || find(harness,"local-tools-scope",[]).visible) { fail("Security page missing or advanced controls not hidden"); return }
          click("navigation-back")
        } else if (navigationStep === 17) {
          if (harness.page !== "") { fail("Security back did not return home"); return }
          click("open-settings")
        } else if (navigationStep === 18) {
          if (harness.page !== "settings" || !find(harness,"context-row",[]).visible || find(harness,"default-agent",[]).visible) { fail("Settings page missing or still owns the default selector"); return }
          click("context-row-open")
        } else if (navigationStep === 19) {
          var windowField=find(harness,"context-read_window_lines",[])
          if (harness.page !== "context" || !windowField || !windowField.visible || windowField.value !== 120 || harness.snapshot.context.settings.read_window_lines !== 120) { fail("Context page missing or not at defaults"); return }
          var idleField=find(harness,"context-idle_cache_ttl_minutes",[]), handoffRow=find(harness,"context-handoff_on_pause",[])
          if (!idleField || !idleField.visible || idleField.value !== 55 || !handoffRow || !handoffRow.visible || !handoffRow.checked || !find(harness,"context-suggest_resume_after_idle",[]) || !find(harness,"context-group_related_subtasks",[]) || !find(harness,"context-explore_with_simple_role",[])) { fail("Context page lacks the long-session settings at their defaults"); return }
          harness.run(["context-set","read_window_lines","200"])
        } else if (navigationStep === 20) {
          // The process can exit before its output reaches the snapshot: wait a few ticks, still fail if it never persists.
          if (harness.page !== "context" || harness.snapshot.context.settings.read_window_lines !== 200) { if (++persistWaits > 12) fail("Context setting was not persisted"); return }
          persistWaits = 0
          click("navigation-back")
        } else if (navigationStep === 21) {
          if (harness.page !== "settings") { fail("Context back did not return to settings"); return }
          click("context-row-open")
        } else if (navigationStep === 22) {
          if (harness.page !== "context" || find(harness,"context-read_window_lines",[]).value !== 200) { fail("Reopened context page lost its saved value"); return }
          harness.run(["context-reset"])
        } else if (navigationStep === 23) {
          if (find(harness,"context-read_window_lines",[]).value !== 120 || harness.snapshot.context.settings.read_window_lines !== 120) { fail("Context reset did not restore defaults"); return }
          click("navigation-back")
        } else if (navigationStep === 24) {
          if (harness.page !== "settings") { fail("Context back did not return to settings"); return }
          click("navigation-back")
        } else if (navigationStep === 25) {
          if (harness.page !== "") { fail("Settings back did not return home"); return }
          prepareSecondProfile.running=true
        } else if (navigationStep === 26) {
          if (prepareSecondProfile.running) return
          harness.run(["status"])
        } else {
          if (second.installation !== "installed") { fail("Second fixture profile not detected"); return }
          console.log("HARNESS_NAVIGATION_POINTER_PASS")
        }
        navigationStep++;return
      }
      if (messageStep < 9) {
        // The smoke runs on a live desktop: a click elsewhere closes the panel and
        // reopening refreshes status, which resets messages. Restart instead of failing.
        if (messageStep > 0 && panelClosures !== messageClosures) { console.log("Panel closed during message checks; restarting them"); messageStep=0; return }
        if (messageStep === 0) {
          messageClosures=panelClosures
          harness.run(["set-default-agent","none"])
          messageStep++;return
        } else if (messageStep === 1) {
          if (!harness.message) { fail("First action message missing"); return }
          firstMessageAt=Date.now();messageStep++;return
        } else if (messageStep === 2) {
          if (Date.now()-firstMessageAt < 2500) return
          harness.run(["launcher-menu","false"])
          messageStep++;return
        } else if (messageStep === 3) {
          secondMessage=harness.message
          if (secondMessage !== "Accesos del menú aplicados a los perfiles seleccionados.") { fail("Second action message missing"); return }
          secondMessageAt=Date.now();messageStep++;return
        } else if (messageStep === 4) {
          if (Date.now()-firstMessageAt < 4250) return
          if (harness.message !== secondMessage) { fail("Old timer cleared the newer action message"); return }
          messageStep++;return
        } else if (messageStep === 5) {
          if (Date.now()-secondMessageAt < 4500) return
          if (harness.message !== "") { fail("Action message did not expire"); return }
          harness.run(["set-default-agent","codex"])
          messageStep++;return
        } else if (messageStep === 6) {
          if (!harness.errorText) { fail("Expected unselected default error missing"); return }
          // The exit handler may write a generic error before the parsed reply lands.
          if (harness.errorText === "La operación no pudo completarse.") return
          expectedError=harness.errorText;secondMessageAt=Date.now();messageStep++;return
        } else if (messageStep === 7) {
          if (Date.now()-secondMessageAt < 4250) return
          if (harness.errorText !== expectedError) { fail("Message timer cleared an error: expected " + JSON.stringify(expectedError) + ", got " + JSON.stringify(harness.errorText) + ", message " + JSON.stringify(harness.message) + ", opened " + harness.opened); return }
          harness.run(["status"]);messageStep++;return
        }
        if (harness.errorText) { fail("Next action did not clear error"); return }
        messageStep++
      }
      if (step === 0) {
        var piAgent = harness.snapshot.agents.find(a => a.id === "pi")
        var piSelector = selector
        var paidDialog = find(harness,"provider-authorization-confirm",[])
        if (!piAgent || !piSelector || !find(harness,"pi-provider-nous",[])) { fail("Pi provider controls missing"); return }
        if (piSetupStep === 0) { piSelector.changed(piAgent.enabled ? [] : ["pi"]); piSetupStep++; return }
        if (piSetupStep === 1) {
          harness.page = "pi"
          if (!piAgent.enabled || harness.snapshot.pi.providers.xai || harness.snapshot.pi.paidApproved) { fail("Pi selection changed providers"); return }
          if (!piAdvancedChecked) {
            var piAdvancedNames=["pi-nous-rpm","pi-nous-margin","pi-nous-quota-save","pi-nous-reset","pi-nous-resume"]
            for (var p=0;p<piAdvancedNames.length;p++) {
              var piAdvancedControl=find(harness,piAdvancedNames[p],[])
              if (!piAdvancedControl || piAdvancedControl.visible) { fail("Pi advanced control must start hidden: " + piAdvancedNames[p]); return }
            }
            find(harness,"pi-advanced",[]).clicked(); piAdvancedChecked=true; return
          }
          if (!find(harness,"pi-nous-rpm",[]).visible || !find(harness,"pi-nous-margin",[]).visible || !find(harness,"pi-nous-quota-save",[]).visible || !find(harness,"pi-nous-reset",[]).visible || !find(harness,"pi-nous-resume",[]).visible) { fail("Pi advanced controls did not open"); return }
          find(harness,"pi-provider-xai",[]).toggled(); piSetupStep++; return
        }
        if (piSetupStep === 2) {
          if (!paidDialog.opened) { fail("External paid provider confirmation absent"); return }
          if (!piPaidCaptured) { piPaidCaptured=true; shot("pi-paid-confirm",true); return }
          paidDialog.canceled(); piSetupStep++; return
        }
        if (piSetupStep === 3) {
          if (harness.snapshot.pi.providers.xai || harness.snapshot.pi.paidApproved) { fail("Cancel activated a paid provider"); return }
          find(harness,"pi-provider-xai",[]).toggled(); piSetupStep++; return
        }
        if (piSetupStep === 4) { paidDialog.confirmed(); piSetupStep++; return }
        if (piSetupStep === 5) {
          if (!harness.snapshot.pi.providers.xai || !harness.snapshot.pi.paidApproved) { fail("Paid scope preference not saved"); return }
          find(harness,"pi-models",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 6) { find(harness,"pi-default-model",[]).changed("xai/grok-fixture"); piSetupStep++; return }
        if (piSetupStep === 7) {
          if (harness.snapshot.pi.model !== "xai/grok-fixture") { fail("Pi default not saved"); return }
          find(harness,"pi-check-xai",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 8) {
          if (harness.snapshot.pi.checks.xai.ready || harness.snapshot.pi.checks.xai.remoteVerified) { fail("Unconfigured xAI reported connected"); return }
          find(harness,"pi-probe-xai",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 9) {
          if (!harness.snapshot.pi.checks.xai.remoteVerified) { fail("Synthetic provider probe not recorded"); return }
          find(harness,"pi-auth",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 10) { find(harness,"pi-provider-xai",[]).toggled(); piSetupStep++; return }
        if (piSetupStep === 11) {
          if (harness.snapshot.pi.model || harness.snapshot.pi.paidApproved || harness.snapshot.pi.providers.xai || harness.snapshot.pi.checks.xai) { fail("Provider disable did not retire managed default/receipt"); return }
          if (Quickshell.env("AI_HARNESS_NOUS_CATALOG_SMOKE") === "1") { find(harness,"pi-provider-nous",[]).toggled(); piSetupStep++; return }
          piSetupStep = 19
        }
        if (piSetupStep === 12) {
          if (!harness.snapshot.pi.nousCatalog.models.length || harness.snapshot.pi.nousCatalog.accountVerified || harness.snapshot.pi.nousCatalog.quotaVerified || !harness.snapshot.pi.quotaConnection.connected) { fail("Nous catalog/guard state invalid"); return }
          if (!find(harness,"pi-nous-rpm",[]).visible) { find(harness,"pi-advanced",[]).clicked(); return }
          find(harness,"pi-nous-rpm",[]).value=60;find(harness,"pi-nous-margin",[]).value=25
          find(harness,"pi-nous-quota-save",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 13) {
          if (harness.snapshot.pi.quota.requestsPerMinute !== 60 || harness.snapshot.pi.quota.marginPercent !== 25) { fail("Declared quota not saved"); return }
          if (!find(harness,"pi-nous-reset",[]).visible) { find(harness,"pi-advanced",[]).clicked(); return }
          find(harness,"pi-nous-reset",[]).text=Quickshell.env("AI_HARNESS_NOUS_RESET")
          find(harness,"pi-nous-resume",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 14) {
          if (!harness.operation.scheduled) { fail("Declared reset not scheduled"); return }
          find(harness,"pi-check-nous",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 15) {
          if (!harness.snapshot.pi.checks.nous.ready || harness.snapshot.pi.checks.nous.remoteVerified) { fail("Local Nous check confused with remote connection"); return }
          find(harness,"pi-probe-nous",[]).clicked(); piSetupStep++; return
        }
        if (piSetupStep === 16) {
          var nousCheck=harness.snapshot.pi.checks.nous
          if (!nousCheck.remoteVerified || find(harness,"pi-nous-connection",[]).text.indexOf(nousCheck.model) === -1 || find(harness,"pi-provider-nous",[]).status.indexOf(nousCheck.model) === -1) { fail("Nous remote result/model not displayed"); return }
          var piScroll=find(harness,"harness-scroll",[])
          var nousControl=find(harness,"pi-provider-nous",[])
          piScroll.contentItem.contentY=nousControl.mapToItem(piScroll.contentItem.contentItem,0,0).y
          shot("pi-nous",true);piSetupStep++;return
        }
        if (piSetupStep === 17) { find(harness,"pi-provider-nous",[]).toggled(); piSetupStep++; return }
        if (piSetupStep === 18) {
          if (harness.snapshot.pi.providers.nous || harness.snapshot.pi.quotaConnection.connected || harness.snapshot.pi.checks.nous) { fail("Nous preference/resource/receipt not retired"); return }
          piSetupStep++
        }
        if (piSetupStep === 19) { piSelector.changed(piAgent.enabled ? [] : ["pi"]); piSetupStep++; return }
        if (piAgent.enabled) { fail("Pi selection not retired"); return }
        harness.page = ""
        var claude = harness.snapshot.agents.find(a => a.id === "claude")
        if (agentSetupStep === 0) {
          if (claude.installation === "installed" || claude.enabled) { fail("Claude must start as a missing lazy wrapper"); return }
          selector.changed(claude.enabled ? [] : ["claude"]); agentSetupStep++; return
        }
        if (agentSetupStep === 1) {
          if (claude.installation !== "installed" || !claude.enabled || claude.manager !== "mise") { fail("Graphical installation/activation failed"); return }
          harness.page = "claude"
          find(harness,"update-agent-claude",[]).clicked(); agentSetupStep++; return
        }
        if (agentSetupStep === 2) {
          if (!harness.operation.changed || claude.version !== "2.1.311") { fail("Graphical CLI update not verified"); return }
          selector.changed(claude.enabled ? [] : ["claude"]); agentSetupStep++; return
        }
        if (claude.enabled || claude.installation !== "installed") { fail("Deactivation removed CLI"); return }
        if (!find(harness, "update-memory", [])) { fail("Runtime update actions missing"); return }
        if (updateSetupStep === 0 && (Quickshell.env("AI_HARNESS_MEMORY_SMOKE") !== "1" || Quickshell.env("AI_HARNESS_UPDATE_SMOKE") === "1")) {
          find(harness, "update-all", []).clicked(); updateSetupStep++; return
        }
        if (updateSetupStep === 1) {
          if (!harness.operation.updates || harness.operation.updates.some(update => update.changed)) { fail("Update changed unexpected installations"); return }
          if (Quickshell.env("AI_HARNESS_UPDATE_SMOKE") === "1" && (harness.operation.updates.length !== 1 || harness.operation.updates[0].outcome !== "current")) { fail("Official runtime noop not confirmed"); return }
          updateSetupStep++
        }
        var memory = harness.snapshot.components.find(c => c.id === "memory")
        var memoryToggle = find(harness, "goal-memory", [])
        if (!memory || !memoryToggle) { fail("Shared memory control missing"); return }
        if (memorySetupStep === 0) {
          memoryToggle.toggled()
          memorySetupStep++
          return
        }
        if (memorySetupStep === 1) {
          if (!memory.enabled || memory.selectedCount !== 0) { fail("Memory preference not prepared without installation"); return }
          memoryToggle.toggled()
          memorySetupStep++
          return
        }
        if (memory.enabled) { fail("Memory preference not disabled"); return }
        if (find(harness, "projectPath", []) || find(harness, "superpowers-codex", [])) { fail("Obsolete controls remain"); return }
        componentToggle.toggled()
        step++
      } else if (step === 1) {
        if (!component.enabled || component.selectedCount !== 0) { fail("Component preference not prepared"); return }
        componentToggle.toggled()
        step++
      } else if (step === 2) {
        if (component.enabled) { fail("Component not disabled"); return }
        selector.changed(["codex"])
        step++
      } else if (step === 3) {
        if (!agent.enabled) { fail("Agent not selected"); return }
        selector.changed(["codex","codex-2"])
        step++
      } else if (step === 4) {
        if (!second.enabled) { fail("Second profile not selected"); return }
        harness.run(["status"])
        step++
      } else if (step === 5) {
        if (!agent.enabled || !second.enabled) { fail("Selection did not survive reload"); return }
        var defaults = find(harness, "default-agent", [])
        var menu = find(harness, "launcher-menu", [])
        var icon = find(harness, "harness-icon", [])
        if (!defaults || !menu || !icon) { fail("Native launcher controls missing"); return }
        if (launcherSetupStep < 5) harness.page = launcherSetupStep === 4 ? "" : "settings"
        if (launcherSetupStep === 0) {
          if (harness.snapshot.launcher.defaultAgent || harness.snapshot.launcher.menu.enabled) { fail("Launcher must start unconfigured"); return }
          defaults.changed("codex-2"); launcherSetupStep++; return
        }
        if (launcherSetupStep === 1) {
          if (harness.snapshot.launcher.defaultAgent !== "codex-2" || !harness.snapshot.launcher.canLaunch) { fail("Default profile not saved"); return }
          menu.toggled(); launcherSetupStep++; return
        }
        if (launcherSetupStep === 2) {
          if (!harness.snapshot.launcher.menu.enabled || !harness.snapshot.launcher.menu.verified) { fail("Menu not prepared"); return }
          icon.pressed(Qt.RightButton); launcherSetupStep++; return
        }
        if (launcherSetupStep === 3) {
          if (!harness.launchRequested || harness.message.indexOf("Codex-2") === -1) { fail("Right-click did not request the default CLI profile"); return }
          harness.page = ""
          find(harness, "launch-default", []).clicked(); launcherSetupStep++; return
        }
        if (launcherSetupStep === 4) {
          if (harness.message.indexOf("Codex-2") === -1) { fail("Explicit CLI button did not request selected profile"); return }
          menu.toggled(); launcherSetupStep++; return
        }
        if (harness.snapshot.launcher.menu.enabled || !harness.snapshot.launcher.menu.verified) { fail("Menu entries not removed"); return }
        if (backupSetupStep === 0) {
          harness.page = "memory"; backupSetupStep++; return
        }
        var destination = find(harness, "memory-backup-destination", [])
        var saveBackup = find(harness, "memory-config-save", [])
        var automatic = find(harness, "memory-backup-auto", [])
        if (!destination || !saveBackup || !automatic) { fail("ECC settings missing"); return }
        if (backupSetupStep === 1) {
          if (harness.snapshot.memory.backupSettings.enabled) { fail("Automatic backups must default off"); return }
          destination.text = Quickshell.env("AI_HARNESS_BACKUP_FOLDER")
          saveBackup.clicked(); backupSetupStep++; return
        }
        if (backupSetupStep === 2) {
          if (harness.snapshot.memory.backupSettings.destination !== destination.text || harness.snapshot.memory.backupSettings.hours !== 24) { if (++persistWaits > 12) fail("Backup settings not persisted"); return }
          persistWaits = 0
          automatic.toggled(); backupSetupStep++; return
        }
        if (backupSetupStep === 3) {
          if (!harness.snapshot.memory.backupSettings.enabled || !harness.snapshot.memory.timer.prepared) { if (++persistWaits > 12) fail("Timer not configured"); return }
          persistWaits = 0
          automatic.toggled(); backupSetupStep++; return
        }
        if (backupSetupStep === 4) {
          if (harness.snapshot.memory.backupSettings.enabled || !harness.snapshot.memory.timer.prepared) { if (++persistWaits > 12) fail("Timer not removed"); return }
          persistWaits = 0
          harness.page = "codex-2"
          if (!find(harness,"authenticate-codex-2",[]).visible) { fail("Native profile authentication action absent"); return }
          harness.page = ""; backupSetupStep++; return
        }
        if (Quickshell.env("AI_HARNESS_MEMORY_SMOKE") === "1") {
          if (memoryFlowStep === 0) { harness.page = "memory"; memoryFlowStep++; return }
          if (memoryFlowStep === 1) {
            if (!harness.snapshot.memory.runtime.installed || harness.snapshot.memory.projects.length !== 1) { fail("Real ECC fixture unavailable"); return }
            find(harness,"memory-export",[]).clicked(); memoryFlowStep++; return
          }
          if (memoryFlowStep === 2) {
            if (!harness.operation.file) { fail("Native ECC export failed"); return }
            find(harness,"memory-archive",[]).text = harness.operation.file
            find(harness,"memory-target-project",[]).text = Quickshell.env("AI_HARNESS_RESTORE_PROJECT")
            find(harness,"memory-preview",[]).clicked(); memoryFlowStep++; return
          }
          var dialog = find(harness,"memory-restore-confirm",[])
          if (memoryFlowStep === 3) {
            if (!harness.operation.restorePreview || harness.operation.restorePreview.newCount !== 1) { fail("Restore preview missing"); return }
            find(harness,"memory-restore",[]).clicked(); memoryFlowStep++; return
          }
          if (memoryFlowStep === 4) {
            if (!dialog || !dialog.opened) { fail("Native restore confirmation did not open"); return }
            shot("restore-confirm", true); memoryFlowStep++; return
          }
          if (memoryFlowStep === 5) { dialog.canceled(); memoryFlowStep++; return }
          if (memoryFlowStep === 6) {
            if (dialog.opened || harness.snapshot.memory.projects.length !== 1) { fail("Cancel changed memory"); return }
            find(harness,"memory-restore",[]).clicked(); memoryFlowStep++; return
          }
          if (memoryFlowStep === 7) { dialog.confirmed(); memoryFlowStep++; return }
          if (memoryFlowStep === 8) {
            if (harness.operation.restoredCount !== 1 || harness.snapshot.memory.projects.length !== 2) { fail("Confirmed restore failed"); return }
            find(harness,"memory-backup-now",[]).clicked(); memoryFlowStep++; return
          }
          if (memoryFlowStep === 9) {
            if (!harness.snapshot.memory.backupState.lastSuccess) { fail("Backup not verified"); return }
            harness.page = ""; memoryFlowStep++; return
          }
        }
        selector.changed(["codex-2"])
        step++
      } else if (step === 6) {
        if (!exercisedDismissal) { exercisedDismissal = true; harness.close(); return }
        if (agent.enabled || !second.enabled || component.enabled) { fail("Agent selection changed component or other profile"); return }
        shot("main")
      } else if (step === 7) {
        harness.page = "tokens"
        step++
      } else if (step === 8) {
        harness.page = "tokens"
        var local = harness.snapshot.components.find(c => c.id === "local-tools")
        var localToggle = find(harness,"goal-tokens-switch",[])
        if (!local || !localToggle || !find(harness,"local-tools-scope",[])) { fail("Local catalog controls missing"); return }
        if (harness.page === "tokens" && !find(harness,"local-tools-scope",[]).visible) {
          find(harness,"advanced-tokens",[]).clicked(); return
        }
        if (localToolStep === 0) {
          if (local.enabled) { fail("Local catalog must default off"); return }
          localToggle.toggled(); localToolStep++; return
        }
        if (localToolStep === 1) {
          if (local.connectedCount !== 1) { fail("Local catalog profile connection failed"); return }
          find(harness,"probe-tool-rg",[]).clicked(); localToolStep++; return
        }
        if (localToolStep === 2) {
          if (!harness.snapshot.localTools.rows.find(r => r.id === "rg").probeVerified) { fail("Local binary probe failed"); return }
          find(harness,"local-tool-rg",[]).changed("off"); localToolStep++; return
        }
        if (localToolStep === 3) {
          if (harness.snapshot.localTools.settings.defaults.rg !== "off") { fail("Local global preference failed"); return }
          find(harness,"local-tools-scope",[]).changed(Quickshell.env("AI_HARNESS_PROJECT_SCOPE")); localToolStep++; return
        }
        if (localToolStep === 4) { find(harness,"local-tool-rg",[]).changed("auto"); localToolStep++; return }
        if (localToolStep === 5) {
          if (harness.snapshot.localTools.settings.projects[Quickshell.env("AI_HARNESS_PROJECT_SCOPE")].rg !== "auto") { fail("Local project preference failed"); return }
          find(harness,"local-tool-rg",[]).changed("inherit"); localToolStep++; return
        }
        if (localToolStep === 6) {
          var uninstallButton = find(harness,"uninstall-tool-rg",[])
          if (!uninstallButton || !uninstallButton.visible) { fail("Uninstall action missing for recorded install"); return }
          var externalButton = find(harness,"uninstall-tool-jq",[])
          if (!externalButton || !externalButton.visible || externalButton.enabled || externalButton.opacity >= 1) { fail("External tool must show a dimmed, disabled uninstall button"); return }
          uninstallButton.clicked(); localToolStep++; return
        }
        if (localToolStep === 7) {
          var uninstallDialog = find(harness,"tool-uninstall-confirm",[])
          if (!uninstallDialog || !uninstallDialog.opened || uninstallDialog.confirmText !== "Desinstalar" || uninstallDialog.message !== "Se quitará: ripgrep. Tus proyectos no cambian.") { fail("Uninstall confirmation missing"); return }
          uninstallDialog.canceled(); localToolStep++; return
        }
        if (localToolStep === 8) {
          if (find(harness,"tool-uninstall-confirm",[]).opened || !harness.snapshot.localTools.rows.find(r => r.id === "rg").uninstallItems.length) { fail("Cancel changed recorded install"); return }
          localToggle.toggled(); localToolStep = 9; return
        }
        if (local.enabled || local.activeCount || !harness.snapshot.localTools.rows.find(r => r.id === "rg").probeVerified) { fail("Local deactivation removed binary probe or left connection active"); return }
        var scroll = find(harness,"harness-scroll",[])
        var catalogView = find(harness,"local-catalog",[])
        scroll.contentItem.contentY = catalogView.mapToItem(scroll.contentItem.contentItem,0,0).y
        shot("tools")
      } else if (step === 9) {
        harness.page = "memory"
        step++
      } else if (step === 10) {
        shot("memory")
      } else if (step === 11) {
        harness.page = "settings"
        step++
      } else if (step === 12) {
        shot("settings")
      } else if (step === 13) {
        if (teamKeying) return
        var teamInfo = harness.snapshot.team
        var teamRow = find(harness,"team-row",[])
        var teamContent = find(harness,"harnessContent",[])
        var teamScroll = find(harness,"harness-scroll",[])
        if (!teamInfo || !teamRow) { fail("Team settings missing"); return }
        if (teamStep === 0) {
          if (teamInfo.enabled || harness.page !== "settings") { fail("Team must start disabled in settings"); return }
          teamContent.commandRequested.connect(function(args) { if (args[0] === "team-models") teamModelCommands++ })
          var teamSwitch = find(harness,"team-row-switch",[])
          pointer.mouseClick(teamSwitch,teamSwitch.width / 2,teamSwitch.height / 2,Qt.LeftButton)
          teamStep++;return
        }
        if (teamStep === 1) {
          if (!teamInfo.enabled || teamInfo.connectedCount !== 1 || harness.page !== "settings") { fail("Team switch must connect without navigating"); return }
          var teamOpen = find(harness,"team-row-open",[])
          pointer.mouseClick(teamOpen,teamOpen.width / 2,teamOpen.height / 2,Qt.LeftButton)
          teamStep++;return
        }
        if (harness.page === "team") {
          var reviewModel = find(harness,"team-role-review",[])
          var reviewEffort = find(harness,"team-effort-review",[])
          if (!reviewModel || !reviewEffort) { fail("Separate team model/effort selectors missing"); return }
          if (!find(harness,"default-agent",[]).visible || !find(harness,"team-label-jefe",[]).visible) { fail("Chief row with the default CLI selector missing from Team"); return }
        }
        if (teamStep === 2) {
          if (harness.page !== "team" || teamModelCommands !== 1 || !teamInfo.options.some(option => option.model === "fixture-medium")) { fail("Opening team did not refresh native models"); return }
          for (var roleId of ["plan","code","review","simple"]) {
            var roleModel = find(harness,"team-role-" + roleId,[])
            var roleEffort = find(harness,"team-effort-" + roleId,[])
            var assignment = teamInfo.roles[roleId]
            var supported = teamInfo.options.find(option => option.agent === assignment.agent && option.model === assignment.model).efforts
            if (JSON.stringify(roleEffort.options.map(option => option.value)) !== JSON.stringify(supported)) { fail("Team effort selector lists invalid levels"); return }
            if (!roleModel.options.some(option => option.label.startsWith("★ ")) || !roleEffort.options.some(option => option.label.startsWith("★ "))) { fail("Team recommendations not marked"); return }
          }
          // Exercise the long model list from the lower edge of the viewport.
          if (reviewModel.options.length <= 8) { fail("Team fixture must exercise a long model list"); return }
          teamScroll.contentItem.contentY=Math.max(0,reviewModel.mapToItem(teamScroll.contentItem.contentItem,0,reviewModel.height).y-teamScroll.height)
          teamStep++;return
        }
        if (teamStep === 3 && !teamHoverChecked) {
          // Ui.Button stays hot while disabled: informational rows must not contain a live one.
          var hoverRow = find(harness,"team-status-codex-2",[])
          var hoverEntry = find(harness,"team-status-codex-2-open",[])
          if (!teamHoverMoved) { pointer.mouseMove(hoverRow,hoverRow.width / 2,hoverRow.height / 2);teamHoverMoved=true;return }
          if (hoverRow.height <= 0 || hoverEntry.visible || hoverEntry.hot) { fail("Informational team row highlights on hover"); return }
          // Keep the pointer off the lists: hovering an option would change keyboard selection.
          var hoverPark = find(harness,"navigation-back",[])
          pointer.mouseMove(hoverPark,hoverPark.width / 2,hoverPark.height / 2)
          teamHoverChecked=true;return
        }
        if (teamStep === 3) {
          teamRowY = find(harness,"team-label-simple",[]).mapToItem(teamScroll.contentItem.contentItem,0,0).y
          teamContentHeight = teamContent.implicitHeight
          var guardField = find(harness,"usage-guard-percent",[])
          if (!guardField || guardField.value !== 90 || !find(harness,"usage-guard-switch",[]).checked) { fail("Usage guard must default to 90 and on"); return }
          teamContentY = teamScroll.contentItem.contentY
          reviewModel.open();teamStep++;return
        }
        if (teamStep === 4) {
          if (!reviewModel.popupOpen || !popupFitsWindow(reviewModel)) { if (++teamWaits < 20) return; fail("Team model popup extends beyond panel or viewport"); return }
          if (find(harness,"team-label-simple",[]).mapToItem(teamScroll.contentItem.contentItem,0,0).y !== teamRowY) { fail("Opening a team popup moved the rows below"); return }
          if (teamContent.implicitHeight !== teamContentHeight) { fail("Opening a team popup stretched the panel"); return }
          if (teamScroll.contentItem.contentY !== teamContentY) { if (++teamWaits < 20) return; fail("Opening a team popup scrolled the page"); return }
          // Native selection writes value before emitting changed; reset below
          // must restore the label as well as the backend preference.
          // The list is alphabetical, so walk from the current model to fixture-medium instead of assuming its neighbour.
          var fromIndex = reviewModel.options.findIndex(option => option.value === reviewModel.value)
          var toIndex = reviewModel.options.findIndex(option => option.value === JSON.stringify(["codex","fixture-medium"]))
          if (fromIndex < 0 || toIndex < 0) { fail("Review model options lack the current or fixture-medium entry"); return }
          teamKeying=true
          for (var move = 0; move < Math.abs(toIndex - fromIndex); move++) pointer.keyClick(toIndex > fromIndex ? Qt.Key_Down : Qt.Key_Up)
          pointer.keyClick(Qt.Key_Return)
          teamKeying=false
          teamStep++;return
        }
        if (teamStep === 5) {
          if (teamInfo.roles.review.model !== "fixture-medium" || teamInfo.roles.review.effort !== "medium" || !teamInfo.roles.review.manual || reviewEffort.options.length !== 1 || reviewEffort.options[0].value !== "medium") { fail("Team model change failed to reset unsupported effort"); return }
          var planEffort = find(harness,"team-effort-plan",[])
          if (!planEffort.options.some(option => option.value === "ultra")) { fail("Manual ultra is unavailable"); return }
          planEffort.open();teamStep++;return
        }
        if (teamStep === 6) {
          var planPopup = find(harness,"team-effort-plan",[])
          if (!planPopup.popupOpen || !popupFitsWindow(planPopup)) { fail("Team effort popup extends beyond window"); return }
          teamKeying=true
          pointer.keyClick(Qt.Key_Down)
          pointer.keyClick(Qt.Key_Down)
          pointer.keyClick(Qt.Key_Return)
          teamKeying=false
          teamStep++;return
        }
        if (teamStep === 7) {
          var teamWarning = find(harness,"team-warning",[])
          if (teamInfo.roles.plan.effort !== "ultra" || !teamWarning.visible || teamWarning.text.indexOf("Ultra hace que Codex reparta el trabajo por su cuenta: rompe el reparto del equipo y gasta tokens por partida doble.") === -1) { fail("Manual ultra warning missing"); return }
          teamScroll.contentItem.contentY=0
          shot("team",true);teamStep++;return
        }
        if (teamStep === 8) {
          var teamRecommended = find(harness,"team-recommended",[])
          pointer.mouseClick(teamRecommended,teamRecommended.width / 2,teamRecommended.height / 2,Qt.LeftButton)
          teamStep++;return
        }
        if (teamStep === 9) {
          if (Object.values(teamInfo.roles).some(role => role.manual) || teamInfo.roles.plan.effort !== "xhigh" || teamInfo.warnings.some(warning => !warning.startsWith("Solo hay una familia disponible")) || teamModelCommands !== 1) { if (++persistWaits > 12) fail("Team recommendations did not reset manual choices"); return }
          persistWaits = 0
          for (var resetRole of ["plan","code","review","simple"]) {
            var resetValue = teamInfo.roles[resetRole]
            if (find(harness,"team-role-" + resetRole,[]).value !== JSON.stringify([resetValue.agent,resetValue.model]) || find(harness,"team-effort-" + resetRole,[]).value !== resetValue.effort) { fail("Team selector labels did not reset to recommended values"); return }
          }
          var teamBack = find(harness,"navigation-back",[])
          pointer.mouseClick(teamBack,teamBack.width / 2,teamBack.height / 2,Qt.LeftButton)
          teamStep++;return
        }
        if (teamStep === 10) {
          if (harness.page !== "settings" || !teamInfo.enabled) { fail("Team back did not return to settings"); return }
          teamRow.toggled();teamStep++;return
        }
        if (teamInfo.enabled || teamInfo.activeCount) { if (++teamWaits < 60) return; fail("Team deactivation left a managed connection"); return }
        if (teamStep === 11) {
          var aiCheckButton = find(harness,"ai-check",[])
          if (!aiCheckButton || !aiCheckButton.visible || (harness.snapshot.aiCheck.results || []).length) { fail("AI check must wait for an explicit click"); return }
          teamScroll.contentItem.contentY=Math.max(0,aiCheckButton.mapToItem(teamScroll.contentItem.contentItem,0,aiCheckButton.height).y-teamScroll.height)
          pointer.mouseClick(aiCheckButton,aiCheckButton.width / 2,aiCheckButton.height / 2,Qt.LeftButton)
          teamStep++;return
        }
        var aiResults = harness.snapshot.aiCheck.results || []
        if (aiResults.length !== 1 || !aiResults[0].ok || aiResults[0].tokens !== 12 || !harness.snapshot.aiCheck.checkedAt) { fail("Explicit AI check did not report the synthetic native result"); return }
        if (harness.page !== aiResults[0].agent) { harness.page = aiResults[0].agent; return }
        var aiResultRow = find(harness,"ai-check-" + aiResults[0].agent,[])
        if (!aiResultRow || !aiResultRow.visible || aiResultRow.status !== "Funciona") { if (++persistWaits > 12) fail("AI check result row missing"); return }
        persistWaits = 0
        if (find(aiResultRow,"ai-check-" + aiResults[0].agent + "-open",[]).visible) { fail("Informational row still reacts to the pointer"); return }
        console.log("HARNESS_TEAM_PASS")
        harness.page = ""
        step++
      } else if (step === 14 && zeroTouchStep === 11) {
        // Restoring: ECC may be missing, and each project moves on its own; `compartido` never asks for a folder.
        harness.page="memory"
        harness.operation={restorePreview:{token:"fixture",newCount:3,conflictCount:0,projectCount:3,requestedArchive:"",projectPath:"",projectMap:{},
          projects:[{key:"a".repeat(64),name:"app",shared:false,newKey:""},{key:"b".repeat(64),name:"app",shared:false,newKey:""},{key:"c".repeat(64),name:"",shared:true,newKey:""}]}}
        zeroTouchStep++; return
      } else if (step === 14 && zeroTouchStep === 12) {
        var first=find(harness,"memory-map-aaaaaaaa",[]);var last=find(harness,"memory-map-bbbbbbbb",[]);var restoreButton=find(harness,"memory-restore",[])
        if (!first||!first.visible||!last||!last.visible||find(harness,"memory-map-cccccccc",[])) { fail("Restore must ask one folder per project, none for compartido"); return }
        if (!restoreButton.enabled) { fail("Restore must follow a current preview"); return }
        first.text="/new/folder"
        if (restoreButton.enabled) { fail("A changed folder must require a new preview"); return }
        first.text=""
        var archiveField=find(harness,"memory-archive",[]);archiveField.text="/backup.zip"
        if (!find(harness,"memory-preview",[]).enabled) { fail("Reviewing a backup must not need ECC already installed"); return }
        archiveField.text=""
        if (harness.snapshot.memory.runtime.installed) { fail("The smoke must run without ECC installed"); return }
        harness.operation={}; harness.page=""; harness.run(["set-agents","none"]); zeroTouchStep++; return
      } else if (step === 14 && zeroTouchStep < 11) {
        // Drift, repair, no second repair, conflict and explicit retry, always on the marked profile.
        var profileHome=Quickshell.env("HOME")+"/.codex"
        var localMissing=function() { return (agent.missingComponents||[]).indexOf("local-tools")!==-1 }
        var waitFor=function(ready,what) {
          if (ready) { zeroTouchWaits=0; return true }
          if (++zeroTouchWaits>40) fail("Zero-touch: "+what)
          return false
        }
        if (zeroTouchStep===0) { harness.page="codex"; zeroTouchStep++; return }
        if (zeroTouchStep===1) {
          var prepare=find(harness,"prepare-agent-codex",[])
          if (!prepare||!prepare.visible) { fail("Prepare action missing"); return }
          prepare.clicked(); zeroTouchStep++; return
        }
        if (zeroTouchStep===2) {
          if (!agent.enabled||harness.snapshot.launcher.defaultAgent!=="codex") { fail("Prepare did not mark and set the default"); return }
          harness.run(["local-tools-all","enable"]); zeroTouchStep++; return
        }
        if (zeroTouchStep===3) {
          if (localMissing()) { fail("Local tools not connected before the drift"); return }
          Quickshell.execDetached(["rm","-f",profileHome+"/config.toml"]); harness.close(); zeroTouchStep++; return
        }
        if (zeroTouchStep===4) {
          if (!harness.opened) { harness.open(); return }
          if (!waitFor(!!harness.operation.reconcile,"opening did not repair the drift")) return
          if (localMissing()||!harness.snapshot.lastReconcile.at) { fail("Repair was not applied or recorded"); return }
          zeroTouchRepairedAt=harness.snapshot.lastReconcile.at
          harness.close(); zeroTouchStep++; return
        }
        if (zeroTouchStep===5) {
          if (harness.opened) return
          harness.open(); zeroTouchWaits=0; zeroTouchStep++; return
        }
        if (zeroTouchStep===6) {
          if (++zeroTouchWaits<6) return
          zeroTouchWaits=0
          if (harness.operation.reconcile||harness.snapshot.lastReconcile.at!==zeroTouchRepairedAt||harness.snapshot.needsReconcile) { fail("Second opening repaired again"); return }
          var foreign=profileHome+"/skills/ai-team-local-tools"
          Quickshell.execDetached(["sh","-c",'rm -rf "$0" && mkdir "$0"',foreign])
          zeroTouchExpectError=true; harness.close(); zeroTouchStep++; return
        }
        if (zeroTouchStep===7) {
          if (!harness.opened) { harness.open(); return }
          if (!waitFor(harness.errorText!=="","a conflict did not show an error")) return
          if (agent.setupState!=="error"||!localMissing()) { fail("Conflict is not visible in the profile"); return }
          harness.page="codex"; zeroTouchStep++; return
        }
        if (zeroTouchStep===8) {
          var issue=find(harness,"setup-issues-codex",[])
          if (!issue||!issue.visible||issue.text.indexOf("skill")===-1) { fail("Profile page does not explain the pending component"); return }
          shot("zero-touch-error",true)
          Quickshell.execDetached(["rm","-rf",profileHome+"/skills/ai-team-local-tools"])
          zeroTouchStep++; return
        }
        if (zeroTouchStep===9) {
          find(harness,"prepare-agent-codex",[]).clicked(); zeroTouchExpectError=false; zeroTouchStep++; return
        }
        if (localMissing()) { fail("Retry did not repair the conflict"); return }
        zeroTouchStep++; return
      } else if (step === 14) {
        shot("main")
      } else if (step === 15) {
        harness.close()
        step++
      } else if (step === 16) {
        if (harness.opened) { fail("Panel did not close"); return }
        harness.open()
        step++
      } else {
        if (!harness.opened) { fail("Panel did not reopen"); return }
        var panel = find(harness, "harnessPanel", [])
        var content = find(harness, "harnessContent", [])
        if (!panel || !content || content.width <= 0 || content.height <= 0) { fail("Popup has no content area"); return }
        if (panel.contentWidth > panel.availableCardWidth || panel.contentHeight > panel.availableCardHeight) { fail("Popup exceeds screen"); return }
        harness.close()
        console.log("HARNESS_SMOKE_PASS")
        Qt.quit()
      }
    }
  }
'''


def valid_terminal_request(request, home, binaries):
    """Smoke contract: native profile/threshold survive per-instance hook arguments."""
    binary = str(binaries/'codex')
    if request[:2] != ['launch', 'terminal'] or 'CODEX_HOME='+str(home/'.codex-2') not in request or binary not in request:
        return False
    index = request.index(binary)
    return request[index+1:index+5] == ['-c', 'model_auto_compact_token_limit=100000',
                                      '-c', 'model_auto_compact_token_limit_scope="total"']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Isolated interaction and screenshots")
    parser.add_argument("--live", action="store_true", help="Allow the smoke test to show native Wayland panels")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/phase1")
    parser.add_argument("--memory-runtime", type=Path, help="Optional existing official ECC package for full GUI recovery test")
    parser.add_argument("--node", type=Path, help="Existing native Node binary, no installer")
    parser.add_argument("--updates", action="store_true", help="Explicit official update check of copied ECC runtime; no model")
    parser.add_argument("--window-smoke", action="store_true", help="Only the synthetic usage-window selection probe, no CLI or profile flow")
    parser.add_argument("--pi-smoke", action="store_true", help="Explicit synthetic Pi UI flow; deferred while closing the core")
    parser.add_argument("--nous-live-catalog", action="store_true", help="Explicit public Nous catalog GET during graphical acceptance, no authentication/inference")
    args = parser.parse_args()
    if bool(args.memory_runtime) != bool(args.node):
        parser.error("Provide both --memory-runtime and --node for the recovery test")
    if args.updates and not args.memory_runtime:
        parser.error("--updates requires an existing official --memory-runtime and --node")
    if args.smoke and not args.live:
        parser.error("Native popup tests require a Wayland session; use --smoke --live")
    omarchy = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
    if not (omarchy / "Ui/qmldir").is_file():
        parser.error("Omarchy shell UI components are required")
    with tempfile.TemporaryDirectory(prefix="ai-team-preview-") as temp:
        directory = Path(temp)
        for name in ("Commons", "Ui"):
            (directory / name).symlink_to(omarchy / name, target_is_directory=True)
        (directory / "Plugin").symlink_to(ROOT, target_is_directory=True)
        host = ('import QtQuick\nimport Quickshell\nimport Quickshell.Io\nimport qs.Commons\n' + ('import QtTest\n' if args.smoke else '') + 'import "Plugin" as Harness\n'
                'ShellRoot {\n'
                '  PanelWindow {\n    implicitHeight: 32\n    color: "transparent"\n'
                '    exclusionMode: ExclusionMode.Ignore\n'
                '    anchors { top: true; left: true; right: true }\n'
                '    Harness.Panel { id: harness; anchors.right: parent.right; width: implicitWidth; height: implicitHeight }\n'
                '  }\n'
                '  Component.onCompleted: harness.open()\n' + ('  TestCase { id: pointer; name: "NavigationPointer"; when: false }\n' if args.smoke else '') + (SMOKE if args.smoke else '') + '\n}\n')
        (directory / "shell.qml").write_text(host)
        env = {**os.environ, "QML_DISABLE_DISK_CACHE": "1", "XDG_CACHE_HOME": str(directory / "cache")}
        if args.smoke:
            home = directory / "home"
            home.mkdir()
            binaries = directory / "agents"
            binaries.mkdir()
            settings_file = directory / "config/omarchy-ai-team/settings.json"
            settings_file.parent.mkdir(parents=True)
            settings_file.write_text('{"schemaVersion":1,"pluginVersion":"previous","language":"es","agents":{},"superpowersEnabled":false,"memoryEnabled":false,"local-toolsEnabled":false,"catalogInstalls":{"rg":{"deps":[]}}}\n')
            for agent in ("claude", "codex", "pi"):
                binary = binaries / agent
                binary.write_text("#!/bin/sh\nexit 99\n")
                binary.chmod(0o755)
            (binaries / "claude").write_text('#!/bin/sh\nmise use -g claude\nexit 99\n')
            for name, version in (("rg", "ripgrep 14.1.1"), ("jq", "jq-1.8.1"),
                                  ("yq", "yq (https://github.com/mikefarah/yq/) version v4.47.2"),
                                  ("ast-grep", "ast-grep 0.40.0")):
                binary = binaries / name
                binary.write_text("#!/bin/sh\n[ \"$1\" = \"--version\" ] || exit 99\nprintf '%s\\n' '" + version + "'\n")
                binary.chmod(0o755)
            (binaries / "codex").write_text('#!/usr/bin/python3\n' + r'''
import json,sys,os,tomllib
from pathlib import Path
if sys.argv[1:2]==['exec']:
 request=json.loads(sys.stdin.read().splitlines()[-1])
 report={'skills':['ai-team-local-tools','ai-team-roles'],'rgVersion':'ripgrep 15.2.0','memoryMarker':request['marker'],'codeModel':'gpt-6.1-sol'}
 print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':8,'output_tokens':4}}))
 sys.exit(0)
if sys.argv[1:]!=['app-server','--stdio']:sys.exit(99)
for line in sys.stdin:
 request=json.loads(line);method=request.get('method')
 if method=='initialize':result={}
 elif method=='config/read':
  file=Path(os.environ['CODEX_HOME'])/'config.toml'
  config=tomllib.loads(file.read_text()) if file.exists() else {}
  result={'layers':[{'name':{'type':'user','file':str(file)},'version':'fixture','config':config}]}
 elif method=='config/batchWrite':
  file=Path(request['params']['filePath']);file.parent.mkdir(parents=True,exist_ok=True)
  for edit in request['params']['edits']:
   if edit['keyPath']=='developer_instructions':
    file.write_text('developer_instructions = '+json.dumps(edit['value'] or '')+'\n')
  result={}
 elif method=='skills/list':
  skills=Path(os.environ['CODEX_HOME'])/'skills'
  result={'data':[{'skills':[{'path':str(p/'SKILL.md'),'enabled':True} for p in skills.iterdir() if (p/'SKILL.md').is_file()] if skills.exists() else []}]}
 elif method=='model/list':
  result={'data':[{'id':model,'isDefault':index==0,'supportedReasoningEfforts':[
   {'reasoningEffort':effort} for effort in efforts]} for index,(model,efforts) in enumerate([
    ('gpt-6.1-sol',['low','medium','high','xhigh','max','ultra']),
    ('gpt-6-luna',['medium','high']),('fixture-medium',['medium']),
    *[(f'fixture-{i:02d}',['medium','high']) for i in range(9)]])]}
 elif method=='initialized':continue
 else:sys.exit(99)
 print(json.dumps({'id':request['id'],'result':result}),flush=True)
''')
            (binaries / "pi").write_text('#!/usr/bin/python3\n' + r'''
import json,sys,os
from pathlib import Path
args=sys.argv[1:]
if args==['--version']:print('1.4.0');sys.exit(0)
if args[:2]==['auth','check']:
 if args[args.index('--provider')+1]=='nous':print(json.dumps({'status':'ready'}));sys.exit(0)
 print(json.dumps({'status':'not_ready','reason':'credentials_not_configured'}));sys.exit(1)
if '--print' in args and '--mode' in args and args[args.index('--mode')+1]=='json':
 print(json.dumps({'type':'message_end','message':{'role':'assistant','stopReason':'stop','content':[{'type':'text','text':'OK'}]}}));sys.exit(0)
if '--mode' not in args or args[args.index('--mode')+1]!='rpc':sys.exit(99)
for line in sys.stdin:
 request=json.loads(line);kind=request['type']
 if kind=='get_commands':data={'commands':[]}
 elif kind=='get_available_models':
  data={'models':[{'id':'grok-fixture','provider':'xai','name':'Synthetic paid model','reasoning':False,'cost':{'input':1,'output':1}}]}
 else:sys.exit(99)
 print(json.dumps({'type':'response','id':request['id'],'command':kind,'success':True,'data':data}))
''')
            mise_root = directory / "managed-mise"
            fake_mise = binaries / "mise"
            fake_mise.write_text('#!/usr/bin/python3\n' + r'''
import json,os,sys
from pathlib import Path
root=Path(os.environ['AI_HARNESS_MISE_ROOT']);root.mkdir(exist_ok=True)
state=root/'version';version=state.read_text() if state.exists() else ''
args=sys.argv[1:]
if args==['ls','--installed','--json']:
 print(json.dumps({'claude':[{'version':version,'installed':True,'active':True,'install_path':str(root/version)}]} if version else {}))
elif args in (['use','--global','--quiet','claude'],['upgrade','--global','--no-prune','claude']):
 version='2.1.310' if args[0]=='use' else '2.1.311'
 binary=root/version/'bin/claude';binary.parent.mkdir(parents=True)
 binary.write_text('#!/usr/bin/python3\nimport sys\nif sys.argv[1:]==["--version"]:print('+repr(version+' (Claude Code)')+')\nelse:sys.exit(99)\n');binary.chmod(0o755)
 state.write_text(version)
else:sys.exit(99)
''')
            fake_mise.chmod(0o755)
            launch_record = directory / "terminal-request.json"
            real_omarchy = "/usr/share/omarchy/bin/omarchy"
            fake_omarchy = binaries / "omarchy"
            fake_omarchy.write_text("#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n"
                "if sys.argv[1:3] == ['launch','terminal']:\n"
                " Path(os.environ['AI_HARNESS_TERMINAL_RECORD']).write_text(json.dumps(sys.argv[1:]))\n"
                "elif sys.argv[1:] == ['restart','shell']:\n"
                " Path(os.environ['AI_HARNESS_TERMINAL_RECORD']+'.restart').write_text('restart')\n"
                "elif sys.argv[1:] == ['version']:\n os.execv(" + repr(real_omarchy) + ",[" + repr(real_omarchy) + ",'version'])\n"
                "else: sys.exit(99)\n")
            fake_omarchy.chmod(0o755)
            backup_folder = directory / "backup destination"
            backup_folder.mkdir()
            systemctl_record = directory / "systemctl-requests.jsonl"
            fake_systemctl = binaries / "systemctl"
            fake_systemctl.write_text("#!/usr/bin/python3\nimport json,os,sys\n"
                "with open(os.environ['AI_HARNESS_SYSTEMCTL_RECORD'],'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n")
            fake_systemctl.chmod(0o755)
            artifacts = args.artifacts.resolve()
            artifacts.mkdir(parents=True, exist_ok=True)
            env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(directory / "config"),
                        "XDG_DATA_HOME": str(directory / "data"),
                        "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CODEX_HOME": str(home / ".codex"),
                        "PI_CODING_AGENT_DIR": str(home/".pi/agent"),
                        "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
                        "AI_HARNESS_SCREENSHOTS": str(artifacts), "AI_HARNESS_TERMINAL_RECORD": str(launch_record),
                        "AI_HARNESS_BACKUP_FOLDER": str(backup_folder), "AI_HARNESS_SYSTEMCTL_RECORD": str(systemctl_record)})
            env["AI_HARNESS_MISE_ROOT"] = str(mise_root)
            if args.window_smoke:
                env["AI_HARNESS_WINDOW_SMOKE"] = "1"
            if args.pi_smoke or args.nous_live_catalog:
                env["AI_HARNESS_PI_SMOKE"]="1"
            if args.nous_live_catalog:
                from datetime import datetime,timezone,timedelta
                env["AI_HARNESS_NOUS_CATALOG_SMOKE"]="1"
                env["AI_HARNESS_NOUS_RESET"]=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()
            sys.path.insert(0,str(ROOT/"backend"))
            import project_scope
            import memory_mcp
            settings_project = home/"settings-project"; settings_project.mkdir()
            scope = project_scope.project_key(settings_project)
            label = directory/"config/omarchy-ai-team/projects"/scope
            label.mkdir(parents=True);memory_mcp.record_project(label,settings_project)
            env["AI_HARNESS_PROJECT_SCOPE"] = scope
            for index in range(12):
                popup_project = home / f"popup-project-{index:02d}"
                popup_project.mkdir()
                popup_label = directory / "config/omarchy-ai-team/projects" / project_scope.project_key(popup_project)
                popup_label.mkdir(parents=True)
                memory_mcp.record_project(popup_label, popup_project)
            if args.memory_runtime:
                import json
                sys.path.insert(0,str(ROOT/"backend"))
                import shared_memory
                runtime = shared_memory.runtime_at(args.memory_runtime,args.node.resolve().as_posix())
                managed = directory/"data/omarchy-ai-team/tools/ecc-memory/node_modules/ecc-universal"
                managed.parent.mkdir(parents=True)
                if args.updates:
                    import shutil
                    shutil.copytree(args.memory_runtime.resolve().parent, managed.parent, dirs_exist_ok=True, symlinks=True)
                    env["AI_HARNESS_UPDATE_SMOKE"] = "1"
                else:
                    managed.symlink_to(args.memory_runtime.resolve(),target_is_directory=True)
                env["PATH"] = str(args.node.resolve().parent) + os.pathsep + env["PATH"]
                project = home/"source-project"; project.mkdir()
                relocated = home/"relocated-project"; relocated.mkdir()
                for folder in (project,relocated):subprocess.run(["git","init","-q",str(folder)],check=True)
                env["AI_HARNESS_MEMORY_SMOKE"]="1";env["AI_HARNESS_RESTORE_PROJECT"]=str(relocated)
                messages=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"gui-fixture","version":"1"}}},
                          {"jsonrpc":"2.0","method":"notifications/initialized"},
                          {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_save","arguments":{"title":"GUI recovery fixture","body":"The accepted synthetic value is 24.","kind":"decision","targetHarnesses":["all"]}}}]
                command=[sys.executable,str(ROOT/"backend/memory_mcp.py"),"--agent","claude","--runtime",runtime["path"],"--node",runtime["node"],"--store",str(directory/"data/omarchy-ai-team/memory")]
                seeded=subprocess.run(command,cwd=project,env=env,input="".join(json.dumps(m)+"\n" for m in messages),text=True,capture_output=True,timeout=15)
                replies=[json.loads(line) for line in seeded.stdout.splitlines()]
                if seeded.returncode or not any(reply.get("id")==2 and bool(reply.get("result",{}).get("content")) and not reply.get("result",{}).get("isError",False) for reply in replies):
                    print("HARNESS_SMOKE_FAIL: official ECC fixture could not be created",file=sys.stderr)
                    print(seeded.stderr[:800],file=sys.stderr)
                    return 1
            try:
                result = subprocess.run(["quickshell", "--no-color", "-p", str(directory)], env=env,
                                        capture_output=True, text=True, timeout=85)
            except subprocess.TimeoutExpired as error:
                print(error.stdout or b"", file=sys.stderr)
                print(error.stderr or b"", file=sys.stderr)
                return 1
            output = result.stdout + result.stderr
            print(output)
            if "HARNESS_SMOKE_FAIL" in output:
                return 1
            if result.returncode != 0 or "HARNESS_SMOKE_PASS" not in output:
                print("HARNESS_SMOKE_FAIL: Quickshell no completó el recorrido; consulta el error de arranque o ejecución anterior.", file=sys.stderr)
                return 1
            if args.window_smoke:
                return 0  # the window probe leaves no install/restart/terminal/timer artifacts to check
            if not all((mise_root/version/'bin/claude').is_file() for version in ('2.1.310','2.1.311')):
                print("HARNESS_SMOKE_FAIL: CLI install/update did not preserve both versions",file=sys.stderr)
                return 1
            if not launch_record.with_name(launch_record.name+'.restart').is_file():
                print("HARNESS_SMOKE_FAIL: updating from a previous version did not restart the shell",file=sys.stderr)
                return 1
            if not launch_record.is_file():
                print("HARNESS_SMOKE_FAIL: no terminal request", file=sys.stderr)
                return 1
            import json
            request = json.loads(launch_record.read_text())
            if not valid_terminal_request(request, home, binaries):
                print("HARNESS_SMOKE_FAIL: wrong terminal/profile request", file=sys.stderr)
                return 1
            if not systemctl_record.is_file():
                print("HARNESS_SMOKE_FAIL: timer was not exercised", file=sys.stderr)
                return 1
            timer_commands = [json.loads(line) for line in systemctl_record.read_text().splitlines()]
            if not any("enable" in command for command in timer_commands) or not any("disable" in command for command in timer_commands):
                print("HARNESS_SMOKE_FAIL: timer enable/disable incomplete", file=sys.stderr)
                return 1
            if list((directory/"config/systemd/user").glob("omarchy-ai-team-backup.*")):
                print("HARNESS_SMOKE_FAIL: timer units were not removed", file=sys.stderr)
                return 1
            if list((directory/"data/applications").glob("ai-team-*.desktop")):
                print("HARNESS_SMOKE_FAIL: menu entries were not removed", file=sys.stderr)
                return 1
            return 0 if result.returncode == 0 and "HARNESS_SMOKE_PASS" in output and "HARNESS_SMOKE_FAIL" not in output else 1
        return subprocess.call(["quickshell", "--no-color", "-p", str(directory)], env=env)


if __name__ == "__main__":
    sys.exit(main())
