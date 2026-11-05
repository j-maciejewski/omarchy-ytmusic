import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui

Item {
  id: root

  property var  homeData: []
  property bool isLoadingHome: false
  property var  foreground: Qt.rgba(1, 1, 1, 1)
  
  signal itemClicked(var item)

  property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.6)

  // Loading state
  Text {
    anchors.centerIn: parent
    text: "Loading Home..."
    color: root.dim
    visible: root.isLoadingHome && (!root.homeData || root.homeData.length === 0)
  }

  ScrollView {
    id: scroll
    anchors.fill: parent
    contentWidth: availableWidth
    clip: true
    visible: !root.isLoadingHome || (root.homeData && root.homeData.length > 0)

    Column {
      id: sections
      width: parent.width
      spacing: Style.space(14)
      topPadding: Style.space(12)
      bottomPadding: Style.space(12)
      // The panel already has side margins; only keep the last card clear of the overlaid scrollbar
      rightPadding: scroll.ScrollBar.vertical.width + Style.space(4)

      Repeater {
        model: root.homeData

        Column {
          width: sections.width - sections.leftPadding - sections.rightPadding
          spacing: Style.space(8)

          Text {
            text: modelData.title || "Section"
            color: root.foreground
            font.bold: true
            font.pixelSize: Style.space(15)
          }

          ListView {
            id: shelf
            // Split the row evenly so whole cards fit with none cut off
            readonly property int cardsPerRow: 4
            readonly property real cardWidth: Math.floor((width - spacing * (cardsPerRow - 1)) / cardsPerRow)

            width: parent.width
            height: cardWidth + Style.space(48)
            orientation: ListView.Horizontal
            spacing: Style.space(10)
            clip: false
            model: modelData.contents

            delegate: Item {
              width: shelf.cardWidth
              height: parent.height

              Rectangle {
                id: card
                anchors.fill: parent
                color: "transparent"

                Column {
                  anchors.fill: parent
                  spacing: Style.space(5)

                  // Thumbnail
                  Rectangle {
                    width: parent.width
                    height: parent.width
                    radius: Style.cornerRadius
                    color: Qt.rgba(0, 0, 0, 0.3)
                    clip: true

                    Image {
                      anchors.fill: parent
                      fillMode: Image.PreserveAspectCrop
                      source: (modelData.thumbnails && modelData.thumbnails.length > 0) 
                              ? modelData.thumbnails[modelData.thumbnails.length - 1].url 
                              : ""
                      asynchronous: true
                    }
                  }

                  // Title
                  Text {
                    width: parent.width
                    text: modelData.title || ""
                    color: root.foreground
                    font.bold: true
                    font.pixelSize: Style.space(11)
                    elide: Text.ElideRight
                    maximumLineCount: 2
                    wrapMode: Text.WordWrap
                  }

                  // Subtitle
                  Text {
                    width: parent.width
                    text: modelData.description || modelData.subtitle || ""
                    color: root.dim
                    font.pixelSize: Style.space(10)
                    elide: Text.ElideRight
                    maximumLineCount: 1
                  }
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.itemClicked(modelData)
                }
              }
            }
          }
        }
      }
    }
  }
}
