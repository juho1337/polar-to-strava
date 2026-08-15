import typer

app = typer.Typer()


@app.command()
def convert():
    print("Convert JSON → TCX")


@app.command()
def upload():
    print("Upload TCX → Strava")


@app.command()
def migrate():
    print("Convert + Upload")


if __name__ == "__main__":
    app()
