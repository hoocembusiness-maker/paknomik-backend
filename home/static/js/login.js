const hide_password_1 = () => {
  var pw_input = document.getElementById("password");
  if (pw_input.type == "password") {
    pw_input.type = "text";
  } else {
    pw_input.type = "password";
  }
};

const hide_password_2 = () => {
  var pw_input = document.getElementById("password_confirmed");
  if (pw_input.type == "password") {
    pw_input.type = "text";
  } else {
    pw_input.type = "password";
  }
};
