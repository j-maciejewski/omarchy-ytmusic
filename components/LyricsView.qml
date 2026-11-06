import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui

Item {
  id: root

  property var lyrics: null
  property bool isLoading: false
  property real position: 0
  property string trackTitle: ""
  property color foreground: Color.foreground
  property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.55)

  readonly property var lines: (lyrics && Array.isArray(lyrics.lines)) ? lyrics.lines : []
  readonly property bool synced: !!(lyrics && lyrics.synced)

  // Last line whose start time has passed; -1 before the first line or for unsynced lyrics
  readonly property int currentLine: {
    if (!synced) return -1
    var idx = -1
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].start_seconds > position) break
      idx = i
    }
    return idx
  }

  signal seekRequested(real seconds)
  signal closeRequested()

  function scrollToCurrent() {
    if (root.currentLine >= 0 && lyricsList.count > 0) {
      Qt.callLater(function() {
        lyricsList.positionViewAtIndex(root.currentLine, ListView.Center)
      })
    }
  }

  onVisibleChanged: {
    if (visible) scrollToCurrent()
  }

  onCurrentLineChanged: {
    if (visible && !lyricsList.moving) scrollToCurrent()
  }

  Column {
    anchors.fill: parent
    spacing: Style.space(8)

    // Header
    Item {
      width: parent.width
      height: Style.space(26)

      Row {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)

        IconButton {
          id: backButton
          text: ""
          buttonSize: Style.space(24)
          toolTipText: "Back to Player"
          foreground: root.foreground
          onClicked: root.closeRequested()
        }

        Text {
          width: parent.width - backButton.width - parent.spacing
          text: "LYRICS" + (root.trackTitle ? " · " + root.trackTitle : "")
          color: root.dim
          font.bold: true
          font.pixelSize: Style.space(11)
          elide: Text.ElideRight
          anchors.verticalCenter: parent.verticalCenter
        }
      }
    }

    // Lyrics List
    Item {
      width: parent.width
      height: parent.height - Style.space(34)

      Text {
        anchors.centerIn: parent
        text: root.isLoading ? "Loading lyrics..." : "No lyrics available for this song."
        color: root.dim
        font.pixelSize: Style.font.caption
        visible: root.lines.length === 0
      }

      ListView {
        id: lyricsList
        anchors.fill: parent
        model: root.lines
        clip: true
        spacing: Style.space(6)
        boundsBehavior: Flickable.StopAtBounds
        visible: root.lines.length > 0

        delegate: Text {
          readonly property bool isCurrent: index === root.currentLine

          width: lyricsList.width
          text: modelData.text || (root.synced ? "♪" : " ")
          wrapMode: Text.WordWrap
          horizontalAlignment: Text.AlignHCenter
          color: isCurrent ? Color.accent : (root.synced && index > root.currentLine ? root.dim : root.foreground)
          font.bold: isCurrent
          font.pixelSize: Style.font.body

          MouseArea {
            anchors.fill: parent
            enabled: root.synced
            cursorShape: Qt.PointingHandCursor
            onClicked: root.seekRequested(modelData.start_seconds)
          }
        }

        footer: Text {
          width: lyricsList.width
          topPadding: Style.space(12)
          text: root.lyrics && root.lyrics.source ? root.lyrics.source : ""
          color: root.dim
          font.pixelSize: Style.space(10)
          horizontalAlignment: Text.AlignHCenter
          visible: text !== ""
        }
      }
    }
  }
}
