'use strict';
// Assemble independently owned views; shared file/model services do not define UI.
TabletHome.mount(document.getElementById('home-root'));
TabletEditor.mount(document.getElementById('editor-root'));
TabletEditor.start();
