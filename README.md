# py_custom_language_sample

# License: MIT

# Status:
- Sample test.

# packages:
- lark 1.3.1
- llvmlite 0.45.1
- python 3.14.0

# Information:
  This is just a sample test. By using the python and library from llvmlite, lark and other libs. To create custom compiler program language.

# vscode:

Open setting and search for terminal.integrated.profiles.windows to edit.

```
"terminal.integrated.profiles.windows": {
//...
"MinGW64 Bash": {
            "path": "C:\\msys64\\usr\\bin\\bash.exe",
            "args": [
                "--login",
                "-i"
            ],
            "env": {
                "MSYSTEM": "MINGW64",
                "CHERE_INVOKING": "1"
            },
            "icon": "terminal-bash",
            "overrideName": true
        },
```
  This will help open terminal to current project folder.

# Build:
  If using the msys 64. It will take a while to config and build. Note it will only work on mingw64 for windows. Have not check others but did check the pacakges site.

```
pacman -Syu
```
update repo and packages.

Required to install gcc for toolchain. Which is needed for llvmlite to work. There would be others if need.

```
pacman -S mingw-w64-x86_64-python
```
```
pacman -S mingw-w64-x86_64-python-pip
```
```
pacman -S mingw-w64-x86_64-python-lark-parser
```
```
pacman -S mingw-w64-x86_64-python-llvmlite
```


# Credits:
- https://llvmlite.readthedocs.io/en/latest/user-guide/binding/examples.html
- Grok AI Agent.