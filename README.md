# frigate_reviewer
uses YOLO vision model to mark false positives as reviewed
![second_eyes](https://github.com/user-attachments/assets/45fa0e2d-e958-4810-b25d-9f1ef6220e2d)
sudo nano /etc/systemd/system/frigate-reviewer.service

[Unit]
Description=Frigate Reviewer Service
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/mnt/raid_volume/frigate_reviewer
Environment=PATH=/mnt/raid_volume/frigate_reviewer/myenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/mnt/raid_volume/frigate_reviewer/myenv/bin/python /mnt/raid_volume/frigate_reviewer/frigate_reviewer.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

Copy and paste the content from above into this file, save and exit (Ctrl+X, then Y, then Enter in nano)

Reload the systemd daemon to recognize the new service:
sudo systemctl daemon-reload

Enable the service to start on boot:
sudo systemctl enable frigate-reviewer.service

Start the service:
sudo systemctl start frigate-reviewer.service

Check the status to make sure its running:
sudo systemctl status frigate-reviewer.service

If you need to see the logs, you can use:
sudo journalctl -u frigate-reviewer.service -f
