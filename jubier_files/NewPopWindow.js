<!--
// Global variables for subwindow reference
var newWindow;

// Generate and fill the new window
function makeNewWindow( url, width, height, location, hasScroll )
{
  if ( hasScroll == "undefined" )
    var showScrollbars = true;
  else
    var showScrollbars = hasScroll;

    // Make sure the window isn't already opened
  if ( ( ! newWindow ) || ( newWindow.closed ) )
  {
    newWindow = openNewWindow( url, width, height, location, showScrollbars );
    if ( ! newWindow.opener )
      newWindow.opener = self;
  }
  else if ( newWindow.focus )
  {
      // The window is already open and focusable, so update it and bring it to the front
    newWindow.resizeTo( width, height );
    newWindow.focus ( );
  }
}

function openNewWindow( url, width, height, location, hasScroll )
{
  if (width >= screen.availWidth)
    width = screen.availWidth - 10;
  if (height >= (screen.availHeight - 50))
    height = screen.availHeight - 50;
  var windowFeatures = "width=" + width + ", height=" + height;
  if ( hasScroll == false )
    windowFeatures += ", resizable, scrollbars=no";
  else
    windowFeatures += ", resizable, scrollbars";
  windowFeatures += ", directories=no, location=no, menubar=no, status=no, toolbar=no";

  if (location == "centered")
  {
    var left = parseInt( ( screen.availWidth / 2 ) - ( width / 2 ) ) - 4;
    var top = parseInt( ( screen.availHeight / 2 ) - ( height / 2 ) ) - 4;
    windowFeatures += ", left=" + left + ", top=" + top + ", screenX=" + left + ", screenY=" + top;
  }
  else if ( location == "topright" )
  {
    var left = screen.availWidth - width - 40;
    var top = 40;
    windowFeatures += ", left=" + left + ", top=" + top + ", screenX=" + left + ", screenY=" + top;
  }

  newWindow = window.open(url, "subWindow", windowFeatures);

  return newWindow;
}
//-->