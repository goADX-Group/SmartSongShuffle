from SongAnalyzer import analyze_song
import SongDataBase 
import SongPicker, SongPlayer
def main(playlist_folder):
    db = SongDataBase.SongDataBase(playlist_folder)#pick the folder you wanna play
    db.analyze_folder()
    picker = SongPicker.SongPicker(db)
    SongPlayer.SongPlayer(db, picker)


#run this script to install all importent libraries
# python -m pip install librosa numpy pandas matplotlib pygame mutagen pillow winsdk
    
#todo: add a seed picker dependant on the date
#todo: make the starting timestamp and starting song on the date and time


if __name__ == "__main__":
    main("path")
    
