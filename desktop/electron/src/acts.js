"use strict";

// The acts the kernel asks of the application, because only this process can do
// them. relaunch and quit are a handover and arrive in that order, so arming
// the restart before ending is the sequence rather than a coincidence; show
// brings the window forward for a clicked toast.
//
// Nothing is answered. Each is performed by ending or by showing, so an
// acknowledgement would have to come from a process on its way out.
function actHandler({ app, showWindow, log = () => {} }) {
  return (act) => {
    if (act === "show" || act === "relaunch" || act === "quit") log(`kernel act: ${act}`);
    if (act === "relaunch") {
      app.relaunch();
      return;
    }
    if (act === "quit") app.quit();
    if (act === "show") showWindow();
  };
}

module.exports = { actHandler };
