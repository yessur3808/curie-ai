-- First verify the user exists
SELECT * FROM users WHERE secret_username = 'curlycoffee3808';

-- Then perform the update with proper quote formatting
UPDATE users 
SET email = 'yaser3808@gmail.com' 
WHERE secret_username = 'curlycoffee3808';