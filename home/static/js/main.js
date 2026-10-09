  const heightListener = () => {
        const windowHeight = window.innerHeight;
        const documentHeight = document.documentElement.scrollHeight;

        if (documentHeight <= windowHeight) {
          // Content is shorter – fixed at bottom
          document.getElementById("paknomik_footer").style.position = "fixed";
          // You might also want to set bottom: 0 and width: 100% to keep it at the bottom
          document.getElementById("paknomik_footer").style.bottom = "0";
          document.getElementById("paknomik_footer").style.width = "100%";
        } else {
          // Content is taller – normal flow
          document.getElementById("paknomik_footer").style.position =
            "relative";
          // Optionally remove the inline bottom/width if they were set
          document.getElementById("paknomik_footer").style.bottom = "";
          document.getElementById("paknomik_footer").style.width = "";
        }
      };